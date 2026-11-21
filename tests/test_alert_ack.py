from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.alert_ack import (
    AlertAckStore,
    is_suppressible_severity,
)
from core.best_next_action import (
    select_best_next_action,
)

# ── pure suppression semantics ───────────────────────────────────────────────

def test_is_suppressible_severity_excludes_objective_breakages():
    assert is_suppressible_severity("medium") is True
    assert is_suppressible_severity("low") is True
    assert is_suppressible_severity("critical") is False
    assert is_suppressible_severity("high") is False
    assert is_suppressible_severity("none") is False


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
        # Не `assert False`: под `python -O` он вырезается, и тест начинает
        # молча проходить там, где обязан падать.
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
