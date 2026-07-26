"""MIR-039 / MIR-040 — a finished run must land the queue row where it belongs.

Two defects, one execution path:

* the cron consumer (``agent_tick.run_tick``) called ``mark_done``
  unconditionally, so a run that was stopped by the budget circuit or blocked
  awaiting approval was recorded as a success; and if ``runtime.run()`` raised,
  the exception escaped past the per-task handling and the row stayed
  ``running`` forever;
* nothing in the live system ever recovered such a row, and the one attempt to
  wire ``recover_stuck`` was reverted because it could reclaim a task that was
  merely slow and run it twice.

The mapping now lives in :mod:`core.task_lifecycle` and both consumers call it.
Recovery is heartbeat-based, attempts-aware, and refuses to run without the
single-instance lock.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.state_integrity import read_state_jsonl_unlocked, rewrite_state_jsonl_unlocked
from core.task_lifecycle import (
    apply_run_exception,
    apply_run_outcome,
    classify_run_outcome,
    recover_orphaned_tasks,
    task_heartbeat,
)
from core.task_queue import TaskQueueStore


class _Lock:
    """Stand-in for app.single_instance.SingleInstanceLock (core stays app-free)."""

    def __init__(self, held: bool) -> None:
        self.held = held


def _age(path: Path, task_id: str, minutes: int) -> None:
    stamp = (datetime.now(tz=timezone.utc) - timedelta(minutes=minutes)).isoformat()
    rows = read_state_jsonl_unlocked(path)
    for row in rows:
        if row.get("id") == task_id:
            row["updated_at"] = stamp
            row["heartbeat_at"] = stamp
    rewrite_state_jsonl_unlocked(path, rows)


# ── The mapping ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "status,stop_reason,expected",
    [
        ("completed", "", "done"),
        ("blocked", "approval required: appr_1", "blocked"),
        ("stopped", "budget exhausted", "failed"),
        ("weird", "", "failed"),
        ("", "", "failed"),
    ],
)
def test_run_outcome_mapping(status, stop_reason, expected):
    assert classify_run_outcome(status=status, stop_reason=stop_reason).outcome == expected


def test_stopped_run_carries_its_stop_reason():
    decision = classify_run_outcome(status="stopped", stop_reason="circuit open")
    assert decision.outcome == "failed"
    assert decision.reason == "circuit open"


def test_completed_run_marks_done(workspace: Path):
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    task = queue.add(goal="ok")
    queue.mark_running(task.id)

    updated, decision = apply_run_outcome(queue, task.id, status="completed")

    assert updated.status == "done"
    assert decision.outcome == "done"


def test_blocked_run_parks_the_task_instead_of_failing_it(workspace: Path):
    """An approval block is not a failure and no retry timer can clear it."""
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    task = queue.add(goal="needs approval", max_attempts=3)
    queue.mark_running(task.id)

    updated, decision = apply_run_outcome(
        queue,
        task.id,
        status="blocked",
        stop_reason="approval required: appr_7",
    )

    assert updated.status == "blocked"
    assert "appr_7" in updated.last_error
    assert decision.outcome == "blocked"
    # Not re-queued, so no consumer picks it up and no attempt is burned.
    assert queue.pending() == []


def test_stopped_run_fails_the_task_with_the_reason(workspace: Path):
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    task = queue.add(goal="budget")
    queue.mark_running(task.id)

    updated, _ = apply_run_outcome(
        queue, task.id, status="stopped", stop_reason="day budget exhausted"
    )

    assert updated.status == "failed"
    assert updated.last_error == "day budget exhausted"


def test_exception_leaves_the_task_terminal_not_running(workspace: Path):
    """The MIR-039 crash case: the row must not stay claimed forever."""
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    task = queue.add(goal="boom")
    queue.mark_running(task.id)

    updated, decision = apply_run_exception(queue, task.id, RuntimeError("kaboom"))

    assert updated.status == "failed"
    assert updated.status != "running"
    assert "RuntimeError: kaboom" in updated.last_error
    assert decision.outcome == "failed"


# ── Heartbeat ────────────────────────────────────────────────────────────────

def test_heartbeat_keeps_a_running_task_out_of_recovery(workspace: Path):
    path = workspace / "tasks.jsonl"
    queue = TaskQueueStore(path)
    task = queue.add(goal="long")
    queue.mark_running(task.id)
    _age(path, task.id, minutes=45)

    with task_heartbeat(queue, task.id, interval_seconds=1.0) as hb:
        # One beat is enough to prove the row is alive.
        deadline = datetime.now(tz=timezone.utc) + timedelta(seconds=10)
        while hb.beats < 1 and datetime.now(tz=timezone.utc) < deadline:
            pass
        assert hb.beats >= 1
        assert recover_orphaned_tasks(queue, lock=_Lock(True)) == []

    assert TaskQueueStore(path).load()[0].status == "running"


def test_heartbeat_stops_when_the_task_finishes(workspace: Path):
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    task = queue.add(goal="short")
    queue.mark_running(task.id)
    queue.mark_done(task.id)

    with task_heartbeat(queue, task.id, interval_seconds=1.0):
        pass

    assert TaskQueueStore(workspace / "tasks.jsonl").load()[0].status == "done"


# ── Guarded recovery ─────────────────────────────────────────────────────────

def test_recovery_refuses_to_run_without_the_lock(workspace: Path):
    """Without exclusivity, 'stale' and 'slow' are the same observation."""
    path = workspace / "tasks.jsonl"
    queue = TaskQueueStore(path)
    task = queue.add(goal="stuck")
    queue.mark_running(task.id)
    _age(path, task.id, minutes=45)

    with pytest.raises(RuntimeError, match="single-instance lock"):
        recover_orphaned_tasks(queue, lock=_Lock(False))

    # And it changed nothing.
    assert TaskQueueStore(path).load()[0].status == "running"


def test_recovery_under_the_lock_finalises_the_orphan(workspace: Path):
    path = workspace / "tasks.jsonl"
    queue = TaskQueueStore(path)
    task = queue.add(goal="stuck", max_attempts=3)
    queue.mark_running(task.id)
    _age(path, task.id, minutes=45)

    recovered = recover_orphaned_tasks(queue, lock=_Lock(True))

    assert [t.id for t in recovered] == [task.id]
    assert recovered[0].status == "pending"


def test_recovery_does_not_resurrect_a_task_past_its_attempt_cap(workspace: Path):
    """The `max_attempts` bypass that made the first wiring unsafe."""
    path = workspace / "tasks.jsonl"
    queue = TaskQueueStore(path)
    task = queue.add(goal="kills-its-process", max_attempts=1)
    queue.mark_running(task.id)
    _age(path, task.id, minutes=45)

    recovered = recover_orphaned_tasks(queue, lock=_Lock(True))

    assert recovered[0].status == "failed"
    assert queue.pending() == []
