"""Tests for the daemon tick health classifier (Layer A: honest health).

The autonomous tick must never read a timed-out / unfinished test run as
"healthy". `_classify_test_health` is the single source of truth that the
daemon uses to translate a raw `tests_result` payload into a verdict.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_tick import (
    _classify_test_health,
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

