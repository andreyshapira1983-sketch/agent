from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.alert_ack import (
    AlertAck,
    AlertAckStore,
    active_acknowledged_actions,
    is_suppressible_severity,
)
from core.best_next_action import (
    is_suppressible_alert,
    select_best_next_action,
)


# ── pure suppression semantics ───────────────────────────────────────────────

def test_is_suppressible_severity_excludes_objective_breakages():
    assert is_suppressible_severity("medium") is True
    assert is_suppressible_severity("low") is True
    assert is_suppressible_severity("critical") is False
    assert is_suppressible_severity("high") is False
    assert is_suppressible_severity("none") is False


def test_is_suppressible_alert_only_advisory_actions():
    assert is_suppressible_alert("review_dry_run_stall") is True
    assert is_suppressible_alert("reduce_inbox_duplicate_debt") is True
    assert is_suppressible_alert("review_inbox_backlog") is True
    # objective breakages are NOT acknowledgeable
    assert is_suppressible_alert("restore_daemon_liveness") is False
    assert is_suppressible_alert("investigate_tick_error") is False
    assert is_suppressible_alert("propose_minimal_test_repair") is False
    assert is_suppressible_alert("resolve_inconclusive_tests") is False
    assert is_suppressible_alert("observe") is False


def test_active_acknowledged_actions_is_pure_and_filters_expired():
    now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    acks = [
        AlertAck(action="review_dry_run_stall", expires_at=None),
        AlertAck(
            action="review_inbox_backlog",
            expires_at=(now - timedelta(hours=1)).isoformat(),  # expired
        ),
        AlertAck(
            action="reduce_inbox_duplicate_debt",
            expires_at=(now + timedelta(hours=1)).isoformat(),  # active
        ),
        AlertAck(action="", expires_at=None),  # empty ignored
    ]
    active = active_acknowledged_actions(acks, now=now)
    assert active == frozenset({"review_dry_run_stall", "reduce_inbox_duplicate_debt"})


# ── BNA suppression behaviour ────────────────────────────────────────────────

def test_acknowledged_dry_run_stall_surfaces_next_real_action():
    # streak 8 alone -> review_dry_run_stall. With a backlog also present and
    # the stall acknowledged, the next real action (backlog) must surface.
    base = select_best_next_action(dry_run_streak=8, inbox_pending=20)
    assert base.action == "review_dry_run_stall"

    suppressed = select_best_next_action(
        dry_run_streak=8,
        inbox_pending=20,
        acknowledged=frozenset({"review_dry_run_stall"}),
    )
    assert suppressed.action == "review_inbox_backlog"


def test_acknowledging_all_advisory_alerts_falls_back_to_observe_with_note():
    action = select_best_next_action(
        dry_run_streak=8,
        inbox_pending=20,
        acknowledged=frozenset({"review_dry_run_stall", "review_inbox_backlog"}),
    )
    assert action.action == "observe"
    # the suppressed alerts are reported, never silently dropped
    joined = " ".join(action.evidence)
    assert "review_dry_run_stall" in joined
    assert "acknowledged" in action.reason.lower()


def test_acknowledgement_cannot_suppress_failing_tests():
    # Even if the operator (wrongly) acknowledges the test-repair action, an
    # objective breakage is critical/high and must NEVER be suppressed.
    action = select_best_next_action(
        tests_health="fail",
        result_status="failed",
        dry_run_streak=8,
        acknowledged=frozenset({"propose_minimal_test_repair", "review_dry_run_stall"}),
    )
    assert action.action == "propose_minimal_test_repair"


def test_acknowledgement_does_not_change_unrelated_pick():
    # Acknowledging an action that is not the current winner has no effect.
    action = select_best_next_action(
        dry_run_streak=8,
        acknowledged=frozenset({"reduce_inbox_duplicate_debt"}),
    )
    assert action.action == "review_dry_run_stall"


# ── store persistence ────────────────────────────────────────────────────────

def test_store_acknowledge_persists_and_reloads(tmp_path: Path):
    path = tmp_path / "alert_acks.jsonl"
    store = AlertAckStore(path=path)
    store.acknowledge(action="review_dry_run_stall", reason="dry-run is intentional")
    # reload from disk
    reloaded = AlertAckStore(path=path)
    assert reloaded.active_actions() == frozenset({"review_dry_run_stall"})
    active = reloaded.list_active()
    assert len(active) == 1
    assert active[0].reason == "dry-run is intentional"


def test_store_acknowledge_replaces_prior_for_same_action(tmp_path: Path):
    path = tmp_path / "alert_acks.jsonl"
    store = AlertAckStore(path=path)
    store.acknowledge(action="review_dry_run_stall", reason="first")
    store.acknowledge(action="review_dry_run_stall", reason="second")
    active = store.list_active()
    assert len(active) == 1
    assert active[0].reason == "second"


def test_store_clear_removes_acknowledgement(tmp_path: Path):
    path = tmp_path / "alert_acks.jsonl"
    store = AlertAckStore(path=path)
    store.acknowledge(action="review_dry_run_stall")
    assert store.clear("review_dry_run_stall") == 1
    assert store.active_actions() == frozenset()
    assert store.clear("review_dry_run_stall") == 0  # idempotent


def test_store_ttl_expiry_drops_from_active(tmp_path: Path):
    path = tmp_path / "alert_acks.jsonl"
    store = AlertAckStore(path=path)
    store.acknowledge(action="review_dry_run_stall", ttl_hours=1)
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    assert store.active_actions(now=future) == frozenset()
    # still present in raw load (auditable), just not active
    assert len(store.load()) == 1


def test_store_acknowledge_rejects_empty_action(tmp_path: Path):
    store = AlertAckStore(path=tmp_path / "alert_acks.jsonl")
    try:
        store.acknowledge(action="   ")
        assert False, "expected ValueError"
    except ValueError:
        pass
