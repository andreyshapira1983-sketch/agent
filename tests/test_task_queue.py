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
