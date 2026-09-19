from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.daemon import DaemonLoop
from core.state_integrity import read_state_jsonl_unlocked, rewrite_state_jsonl_unlocked
from core.task_queue import TaskQueueStore


def test_task_queue_persists_added_task(workspace: Path):
    path = workspace / "data" / "tasks.jsonl"
    queue = TaskQueueStore(path)

    task = queue.add(goal="project health", include_tests=False, limit=2)

    reloaded = TaskQueueStore(path).load()
    assert len(reloaded) == 1
    assert reloaded[0].id == task.id
    assert reloaded[0].goal == "project health"
    assert not reloaded[0].include_tests
    assert reloaded[0].limit == 2


def test_task_added_callback_runs_after_task_is_persisted(workspace: Path):
    path = workspace / "tasks.jsonl"
    seen: list[str] = []

    def wake(task) -> None:
        persisted = TaskQueueStore(path).get(task.id)
        assert persisted is not None
        seen.append(task.id)

    queue = TaskQueueStore(path, on_task_added=wake)

    task = queue.add(goal="wake daemon")

    assert seen == [task.id]


def test_paused_checkpoint_notifies_task_added_callback(workspace: Path):
    seen = []
    queue = TaskQueueStore(
        workspace / "tasks.jsonl",
        on_task_added=seen.append,
    )

    task = queue.add_paused_checkpoint(
        goal="resume later",
        report={"stop_reason": "budget_exhausted"},
    )

    assert seen == [task]


def test_task_added_callback_failure_keeps_persisted_task(
    workspace: Path,
    caplog,
):
    path = workspace / "tasks.jsonl"

    def broken_wake(_task) -> None:
        raise RuntimeError("loop unavailable")

    queue = TaskQueueStore(path, on_task_added=broken_wake)

    with caplog.at_level(logging.ERROR, logger="core.task_queue"):
        task = queue.add(goal="still durable")

    assert TaskQueueStore(path).get(task.id) == task
    assert "runtime task wake callback failed" in caplog.text


def test_new_runtime_task_wakes_daemon_immediately(workspace: Path):
    async def scenario() -> None:
        reasons_seen: list[str] = []
        daemon: DaemonLoop

        async def handle_wake(reasons: list[str]) -> None:
            reasons_seen.extend(reasons)
            daemon.request_stop()

        daemon = DaemonLoop(handle_wake)
        queue = TaskQueueStore(
            workspace / "tasks.jsonl",
            on_task_added=lambda task: daemon.wake_threadsafe(
                f"runtime-task:{task.id}"
            ),
        )
        runner = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)

        task = queue.add(goal="dispatch now")
        await asyncio.wait_for(runner, timeout=1)

        assert reasons_seen == [f"runtime-task:{task.id}"]

    asyncio.run(scenario())


def test_pending_filters_future_tasks(workspace: Path):
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    due = queue.add(goal="due", run_after=now - timedelta(minutes=1))
    queue.add(goal="future", run_after=now + timedelta(minutes=1))

    pending = queue.pending(now=now)

    assert [task.id for task in pending] == [due.id]


def test_running_done_and_failed_transitions(workspace: Path):
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    task = queue.add(goal="repair", max_attempts=1)

    running = queue.mark_running(task.id)
    assert running.status == "running"
    assert running.attempts == 1

    failed = queue.mark_failed(task.id, error="red tests")
    assert failed.status == "failed"
    assert failed.last_error == "red tests"

    done_task = queue.add(goal="green")
    queue.mark_running(done_task.id)
    done = queue.mark_done(done_task.id, report={"status": "completed"})
    assert done.status == "done"
    assert done.last_report == {"status": "completed"}


def test_failed_task_requeues_until_max_attempts(workspace: Path):
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    task = queue.add(goal="flaky", max_attempts=2)

    queue.mark_running(task.id)
    retry = queue.mark_failed(task.id, error="first failure")

    assert retry.status == "pending"
    assert retry.attempts == 1


def test_summary_counts_statuses(workspace: Path):
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    task = queue.add(goal="one")
    queue.add(goal="two")
    queue.mark_running(task.id)
    queue.mark_done(task.id)

    summary = queue.summary()

    assert summary["total"] == 2
    assert summary["statuses"]["done"] == 1
    assert summary["statuses"]["pending"] == 1


def test_task_queue_skips_invalid_records_and_parses_string_booleans(workspace: Path):
    path = workspace / "tasks.jsonl"
    valid = {
        "kind": "auto_run",
        "goal": "valid",
        "status": "pending",
        "dry_run": "false",
        "include_tests": "false",
    }
    invalid = {
        "kind": "unexpected",
        "goal": "invalid",
        "status": "pending",
    }
    path.write_text(
        "\n".join(
            [
                json.dumps(valid),
                json.dumps(invalid),
                "{not-json",
            ]
        ),
        encoding="utf-8",
    )

    tasks = TaskQueueStore(path).load()

    assert len(tasks) == 1
    assert tasks[0].goal == "valid"
    assert tasks[0].dry_run is False
    assert tasks[0].include_tests is False



def _backdate_updated_at(path: Path, task_id: str, minutes_ago: int) -> None:
    """Age a task: both ``updated_at`` and the heartbeat that supersedes it.

    Recovery measures liveness on ``heartbeat_at`` since MIR-040 — a task
    running for 45 minutes and still beating is alive, and reclaiming it would
    start a second execution of the same work. Ageing only ``updated_at`` no
    longer makes a task look orphaned, which is the point.
    """
    backdated = (
        datetime.now(tz=timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat()
    rows = read_state_jsonl_unlocked(path)
    for row in rows:
        if row.get("id") == task_id:
            row["updated_at"] = backdated
            row["heartbeat_at"] = backdated
    rewrite_state_jsonl_unlocked(path, rows)


def test_recover_stuck_finalises_orphan_that_exhausted_its_attempts(workspace: Path):
    """Default ``max_attempts=1``: one claim spends the budget.

    The pre-MIR-040 version reset such a task straight to ``pending``, so a task
    that kills its own process was resurrected and ran one attempt past the cap.
    """
    path = workspace / "tasks.jsonl"
    queue = TaskQueueStore(path)
    task = queue.add(goal="stuck")
    queue.mark_running(task.id)
    _backdate_updated_at(path, task.id, minutes_ago=45)

    recovered = queue.recover_stuck(timeout_minutes=30)

    assert len(recovered) == 1
    assert recovered[0].id == task.id
    assert recovered[0].status == "failed"
    assert "orphaned" in recovered[0].last_error.lower()

    reloaded = TaskQueueStore(path).load()
    assert reloaded[0].status == "failed"


def test_recover_stuck_requeues_orphan_with_attempts_left(workspace: Path):
    """With budget remaining the orphan is re-queued — behind the backoff."""
    path = workspace / "tasks.jsonl"
    queue = TaskQueueStore(path)
    task = queue.add(goal="stuck", max_attempts=3)
    queue.mark_running(task.id)
    _backdate_updated_at(path, task.id, minutes_ago=45)

    recovered = queue.recover_stuck(timeout_minutes=30)

    assert len(recovered) == 1
    assert recovered[0].status == "pending"
    assert "orphaned" in recovered[0].last_error.lower()
    # Not immediately eligible again: a task that killed its process must not
    # be hot-retried on the next tick.
    assert queue.pending() == []


def test_recover_stuck_leaves_a_beating_long_task_alone(workspace: Path):
    """The hazard that reverted the first wiring: slow is not dead."""
    path = workspace / "tasks.jsonl"
    queue = TaskQueueStore(path)
    task = queue.add(goal="slow-but-alive")
    queue.mark_running(task.id)
    # 45 minutes of work, still beating.
    _backdate_updated_at(path, task.id, minutes_ago=45)
    queue.heartbeat(task.id)

    recovered = queue.recover_stuck(timeout_minutes=30)

    assert recovered == []
    assert TaskQueueStore(path).load()[0].status == "running"


def test_recover_stuck_leaves_fresh_running_task_alone(workspace: Path):
    path = workspace / "tasks.jsonl"
    queue = TaskQueueStore(path)
    task = queue.add(goal="fresh")
    queue.mark_running(task.id)

    recovered = queue.recover_stuck(timeout_minutes=30)

    assert recovered == []
    reloaded = TaskQueueStore(path).load()
    assert reloaded[0].status == "running"


def test_recover_stuck_ignores_non_running_tasks(workspace: Path):
    path = workspace / "tasks.jsonl"
    queue = TaskQueueStore(path)
    pending_task = queue.add(goal="pending-old")
    _backdate_updated_at(path, pending_task.id, minutes_ago=120)

    recovered = queue.recover_stuck(timeout_minutes=30)

    assert recovered == []
    reloaded = TaskQueueStore(path).load()
    assert reloaded[0].status == "pending"


def test_recover_stuck_treats_unparseable_timestamp_as_very_old(workspace: Path):
    path = workspace / "tasks.jsonl"
    queue = TaskQueueStore(path)
    task = queue.add(goal="garbled", max_attempts=3)
    queue.mark_running(task.id)
    # corrupt both liveness fields directly
    rows = read_state_jsonl_unlocked(path)
    rows[0]["updated_at"] = "not-a-timestamp"
    rows[0]["heartbeat_at"] = "not-a-timestamp"
    rewrite_state_jsonl_unlocked(path, rows)

    recovered = queue.recover_stuck(timeout_minutes=30)

    assert len(recovered) == 1
    assert recovered[0].status == "pending"


def test_recover_stuck_falls_back_to_updated_at_on_legacy_rows(workspace: Path):
    """Rows written before heartbeats existed still recover."""
    path = workspace / "tasks.jsonl"
    queue = TaskQueueStore(path)
    task = queue.add(goal="legacy", max_attempts=3)
    queue.mark_running(task.id)
    backdated = (
        datetime.now(tz=timezone.utc) - timedelta(minutes=45)
    ).isoformat()
    rows = read_state_jsonl_unlocked(path)
    rows[0]["updated_at"] = backdated
    rows[0].pop("heartbeat_at", None)  # the shape of an old row
    rewrite_state_jsonl_unlocked(path, rows)

    recovered = queue.recover_stuck(timeout_minutes=30)

    assert len(recovered) == 1
    assert recovered[0].status == "pending"


def test_heartbeat_only_touches_a_running_task(workspace: Path):
    path = workspace / "tasks.jsonl"
    queue = TaskQueueStore(path)
    task = queue.add(goal="finished")
    queue.mark_running(task.id)
    queue.mark_done(task.id)

    assert queue.heartbeat(task.id) is None
    assert queue.heartbeat("no-such-task") is None
    assert TaskQueueStore(path).load()[0].status == "done"


def test_blocked_task_waits_for_the_operator(workspace: Path):
    path = workspace / "tasks.jsonl"
    queue = TaskQueueStore(path)
    task = queue.add(goal="needs approval")
    queue.mark_running(task.id)

    blocked = queue.mark_blocked(task.id, reason="approval required: appr_1")
    assert blocked.status == "blocked"
    assert "approval required" in blocked.last_error
    # No consumer may pick it up, and no timer can unblock it.
    assert queue.pending() == []
    assert queue.summary()["statuses"]["blocked"] == 1

    released = queue.unblock(task.id)
    assert released.status == "pending"
    assert [t.id for t in queue.pending()] == [task.id]


def test_unblock_refuses_a_task_that_is_not_blocked(workspace: Path):
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    task = queue.add(goal="ordinary")

    with pytest.raises(ValueError, match="not blocked"):
        queue.unblock(task.id)


def test_recover_stuck_returns_empty_when_no_tasks(workspace: Path):
    queue = TaskQueueStore(workspace / "tasks.jsonl")

    assert queue.recover_stuck(timeout_minutes=30) == []
