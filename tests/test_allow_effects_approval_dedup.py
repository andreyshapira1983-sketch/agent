"""MIR-072 — re-running a blocked non-dry session must not spam the inbox.

Measured live by the operator (2026-08-03): sixteen consecutive
`:work-session` invocations, each blocked on `autonomous_runtime.allow_effects`
approval, created SIXTEEN identical pending items (pending 1 → 16). The inbox
has had a structural duplicate guard since the daemon's proposed_task clusters
(`ApprovalInbox.add(dedup_key=...)`) — but this call site never passed a key,
so the guard the defect class already owns simply was not consulted.

Contract: while an equivalent allow-effects request is pending, a repeat run
returns the SAME item id in its stop reason and adds nothing; a different goal
is a different request; a decided (approved/rejected) item stops deduping so a
new run can ask again.
"""
from __future__ import annotations

from pathlib import Path

from core.approval_inbox import ApprovalInbox
from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig


class _NullLog:
    def log(self, *a, **k):
        pass


class _BombAgent:
    """The blocked path may journal, but must never run anything."""

    log = _NullLog()

    def __getattr__(self, name):  # pragma: no cover — proves non-use
        raise AssertionError(f"agent touched on the blocked path: {name}")


def _runtime(tmp_path: Path) -> AutonomousRuntime:
    return AutonomousRuntime(
        _BombAgent(),
        workspace=tmp_path,
        approval_inbox=ApprovalInbox(path=tmp_path / "approval_inbox.jsonl"),
    )


def _run_blocked(rt: AutonomousRuntime, goal: str):
    report = rt.run(AutonomousRuntimeConfig(goal=goal, dry_run=False))
    assert report.status == "blocked"
    return report


def test_repeat_blocked_runs_share_one_pending_item(tmp_path):
    rt = _runtime(tmp_path)
    first = _run_blocked(rt, "project health")
    for _ in range(15):
        again = _run_blocked(rt, "project health")
        assert again.stop_reason == first.stop_reason, (
            "a repeat run must point at the SAME pending approval"
        )
    snapshot = rt.approval_inbox.snapshot()
    assert snapshot["pending"] == 1, (
        f"измерено вживую: 16 повторов дали 16 заявок; должно быть 1, "
        f"получили {snapshot['pending']}"
    )


def test_a_different_goal_is_a_different_request(tmp_path):
    rt = _runtime(tmp_path)
    a = _run_blocked(rt, "project health")
    b = _run_blocked(rt, "проверь журнал и ответь числом")
    assert a.stop_reason != b.stop_reason
    assert rt.approval_inbox.snapshot()["pending"] == 2


def test_secret_bearing_goal_still_dedups(tmp_path):
    """Review round #284 (Copilot): the stored payload is redacted, so a key
    carrying raw goal text stops matching once the goal contains a secret —
    spam would return exactly for sensitive goals. The key must therefore be
    derived from a stable non-sensitive form (hash), not the raw text."""
    import hashlib

    rt = _runtime(tmp_path)
    secret_goal = "проверь ключ " + "AKIA" + "IOSFODNN" + "7EXAMPLE" + " в конфиге"
    first = _run_blocked(rt, secret_goal)
    # Reopen the PERSISTED inbox (post-merge review round #284): the failure
    # mode lives in what is stored on disk, so the second run must dedup
    # against the reloaded item, not an in-memory leftover.
    rt = _runtime(tmp_path)
    again = _run_blocked(rt, secret_goal)
    assert again.stop_reason == first.stop_reason
    pending = rt.approval_inbox.pending()
    assert len(pending) == 1
    stored_key = (pending[0].payload or {}).get("dedup_key", "")
    expected = (
        "autonomous_runtime.allow_effects:"
        + hashlib.sha256(secret_goal.encode("utf-8")).hexdigest()[:16]
    )
    assert stored_key == expected, "хранимый ключ обязан быть хэшем цели"
    assert secret_goal not in stored_key
    assert "IOSFODNN" not in stored_key, "текст секрета не должен жить в ключе"


def test_dedup_hit_does_not_burn_approval_budget(tmp_path):
    """Review round #284 (Codacy): a retry that adds nothing must not reserve
    approval_requests budget — otherwise honest retries starve real asks."""
    from core.budget_governor import BudgetGovernor

    rt = _runtime(tmp_path)
    budget = BudgetGovernor()
    rt.run(AutonomousRuntimeConfig(goal="project health", dry_run=False), budget=budget)
    rt.run(AutonomousRuntimeConfig(goal="project health", dry_run=False), budget=budget)
    assert budget.snapshot()["used"]["approval_requests"] == 1


def test_a_decided_item_stops_deduping(tmp_path):
    rt = _runtime(tmp_path)
    first = _run_blocked(rt, "project health")
    item_id = first.stop_reason.split(": ", 1)[1]
    rt.approval_inbox.deny(item_id)
    again = _run_blocked(rt, "project health")
    assert again.stop_reason != first.stop_reason, (
        "после решения оператора новый запуск вправе спросить заново"
    )
    assert rt.approval_inbox.snapshot()["pending"] == 1
