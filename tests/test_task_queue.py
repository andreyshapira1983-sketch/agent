from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    """Rewrite a single task's ``updated_at`` to be ``minutes_ago`` minutes in the past."""
    backdated = (
        datetime.now(tz=timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat()
    rows = read_state_jsonl_unlocked(path)
    for row in rows:
        if row.get("id") == task_id:
            row["updated_at"] = backdated
    rewrite_state_jsonl_unlocked(path, rows)


def test_recover_stuck_resets_running_task_older_than_timeout(workspace: Path):
    path = workspace / "tasks.jsonl"
    queue = TaskQueueStore(path)
    task = queue.add(goal="stuck")
    queue.mark_running(task.id)
    _backdate_updated_at(path, task.id, minutes_ago=45)

    recovered = queue.recover_stuck(timeout_minutes=30)

    assert len(recovered) == 1
    assert recovered[0].id == task.id
    assert recovered[0].status == "pending"
    assert "stuck" in recovered[0].last_error.lower()

    reloaded = TaskQueueStore(path).load()
    assert reloaded[0].status == "pending"


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
    task = queue.add(goal="garbled")
    queue.mark_running(task.id)
    # corrupt updated_at directly
    rows = read_state_jsonl_unlocked(path)
    rows[0]["updated_at"] = "not-a-timestamp"
    rewrite_state_jsonl_unlocked(path, rows)

    recovered = queue.recover_stuck(timeout_minutes=30)

    assert len(recovered) == 1
    assert recovered[0].status == "pending"


def test_recover_stuck_returns_empty_when_no_tasks(workspace: Path):
    queue = TaskQueueStore(workspace / "tasks.jsonl")

    assert queue.recover_stuck(timeout_minutes=30) == []
