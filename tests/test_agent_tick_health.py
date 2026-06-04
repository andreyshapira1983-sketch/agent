"""Tests for the daemon tick health classifier (Layer A: honest health).

The autonomous tick must never read a timed-out / unfinished test run as
"healthy". `_classify_test_health` is the single source of truth that the
daemon uses to translate a raw `tests_result` payload into a verdict.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_tick import (
    _classify_test_health,
    _dry_run_visibility,
    _heartbeat_age_seconds,
    _is_stale,
    _read_heartbeat,
    _write_heartbeat,
    EXPECTED_TICK_INTERVAL_SECONDS,
    STALENESS_FACTOR,
)


def test_no_tests_result_is_none():
    assert _classify_test_health(None) == "none"
    assert _classify_test_health({}) == "none"


def test_clean_pass_is_pass():
    result = {"exit_code": 0, "timed_out": False, "passed": 1863, "failed": 0, "errors": 0}
    assert _classify_test_health(result) == "pass"


def test_real_failure_is_fail():
    result = {"exit_code": 1, "timed_out": False, "passed": 100, "failed": 2, "errors": 0}
    assert _classify_test_health(result) == "fail"


def test_errors_count_as_fail():
    result = {"exit_code": 1, "timed_out": False, "passed": 100, "failed": 0, "errors": 3}
    assert _classify_test_health(result) == "fail"


def test_timeout_is_inconclusive_not_pass():
    # This is the Layer A bug: passed=0/failed=0 + timed_out=True was read as
    # "healthy". It must be inconclusive.
    result = {"exit_code": None, "timed_out": True, "passed": 0, "failed": 0, "errors": 0}
    assert _classify_test_health(result) == "inconclusive"


def test_missing_exit_code_is_inconclusive():
    result = {"exit_code": None, "timed_out": False, "passed": 0, "failed": 0, "errors": 0}
    assert _classify_test_health(result) == "inconclusive"


def test_zero_collected_is_inconclusive():
    # exit_code present, no failures, but nothing actually ran.
    result = {"exit_code": 0, "timed_out": False, "passed": 0, "failed": 0, "errors": 0}
    assert _classify_test_health(result) == "inconclusive"


# ── Layer B: heartbeat / last_tick_age ────────────────────────────────────────


def test_heartbeat_roundtrip_and_age(workspace):
    _write_heartbeat(workspace, {"event": "tick_complete", "tests_health": "pass"})
    hb = _read_heartbeat(workspace)
    assert hb is not None
    assert hb["event"] == "tick_complete"
    age = _heartbeat_age_seconds(hb)
    assert age is not None
    assert age < 5  # just written


def test_read_heartbeat_missing_returns_none(workspace):
    assert _read_heartbeat(workspace) is None


def test_heartbeat_age_none_when_no_heartbeat():
    assert _heartbeat_age_seconds(None) is None
    assert _heartbeat_age_seconds({}) is None


def test_fresh_heartbeat_is_not_stale():
    now = datetime.now(timezone.utc)
    hb = {"ts": now.isoformat()}
    age = _heartbeat_age_seconds(hb, now=now + timedelta(minutes=1))
    assert _is_stale(age) is False


def test_old_heartbeat_is_stale():
    now = datetime.now(timezone.utc)
    hb = {"ts": now.isoformat()}
    overdue = now + timedelta(
        seconds=EXPECTED_TICK_INTERVAL_SECONDS * STALENESS_FACTOR + 60
    )
    age = _heartbeat_age_seconds(hb, now=overdue)
    assert _is_stale(age) is True


def test_missing_age_is_treated_as_stale():
    # No heartbeat at all means we cannot prove liveness → stale.
    assert _is_stale(None) is True


# ── dry-run visibility (observability only, no behaviour change) ───────────────


def test_dry_run_visibility_reports_disabled_effects_and_zero_processed():
    # Requirement 1: dry_run=True -> mode=dry_run, effects=disabled,
    # processed_effects=0.
    v = _dry_run_visibility(dry_run=True, previous_streak=0)
    assert v["mode"] == "dry_run"
    assert v["effects"] == "disabled"
    assert v["processed_effects"] == 0
    assert v["dry_run_streak"] == 1  # first dry-run tick


def test_dry_run_streak_grows_across_consecutive_dry_run_ticks():
    # Requirement 2: several dry-run ticks in a row -> streak increments.
    streak = 0
    streaks = []
    for _ in range(4):
        v = _dry_run_visibility(dry_run=True, previous_streak=streak)
        streak = v["dry_run_streak"]
        streaks.append(streak)
    assert streaks == [1, 2, 3, 4]


def test_live_tick_resets_streak_and_does_not_imply_applied_effects():
    # Requirement 3: a non-dry-run tick resets the streak (does not increment).
    v = _dry_run_visibility(dry_run=False, previous_streak=7)
    assert v["mode"] == "live"
    assert v["dry_run_streak"] == 0
    # "enabled" means policy-allowed, NOT "already applied".
    assert v["effects"] == "enabled"
    # Requirement 4: even live, nothing was actually applied this tick.
    assert v["processed_effects"] == 0


def test_dry_run_visibility_never_claims_effects_were_applied():
    # Requirement 4: the summary fields must not imply effects ran.
    for dry in (True, False):
        v = _dry_run_visibility(dry_run=dry, previous_streak=3)
        assert v["processed_effects"] == 0
        assert "applied" not in v  # no field asserting application
        assert v["effects"] in {"disabled", "enabled"}


def test_failed_or_inconclusive_dry_run_tick_still_counts_as_dry_run_tick():
    # A dry-run tick that failed/was inconclusive is STILL a dry-run tick: the
    # streak grows. Health is a separate signal, not mixed into the streak.
    v = _dry_run_visibility(dry_run=True, previous_streak=5)
    assert v["mode"] == "dry_run"
    assert v["dry_run_streak"] == 6


def test_dry_run_helper_is_pure_and_echoes_processed_effects():
    # The helper takes primitives only and echoes processed_effects back,
    # clamped to a non-negative int.
    v = _dry_run_visibility(dry_run=False, previous_streak=2, processed_effects=3)
    assert v["processed_effects"] == 3
    # Negative / garbage processed_effects clamp to 0 without raising.
    assert _dry_run_visibility(dry_run=True, previous_streak=0, processed_effects=-4)["processed_effects"] == 0


def test_dry_run_streak_tolerates_corrupt_previous_value():
    # A garbled previous streak must not crash the math; treat as fresh start.
    v = _dry_run_visibility(dry_run=True, previous_streak="oops")  # type: ignore[arg-type]
    assert v["dry_run_streak"] == 1



