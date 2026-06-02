from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.scheduler import SchedulerStore
from core.task_queue import TaskQueueStore


def test_scheduler_persists_schedule(workspace: Path):
    store = SchedulerStore(workspace / "data" / "schedules.jsonl")

    schedule = store.add(name="health", goal="project health", every_minutes=15)

    reloaded = SchedulerStore(store.path).load()
    assert len(reloaded) == 1
    assert reloaded[0].id == schedule.id
    assert reloaded[0].every_minutes == 15


def test_due_returns_only_active_due_schedules(workspace: Path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = SchedulerStore(workspace / "schedules.jsonl")
    due = store.add(
        name="due",
        goal="due goal",
        every_minutes=10,
        start_at=now - timedelta(minutes=1),
    )
    future = store.add(
        name="future",
        goal="future goal",
        every_minutes=10,
        start_at=now + timedelta(minutes=1),
    )
    store.pause(future.id)

    due_now = store.due(now=now)

    assert [schedule.id for schedule in due_now] == [due.id]


def test_tick_enqueues_due_tasks_and_advances_schedule(workspace: Path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    schedules = SchedulerStore(workspace / "schedules.jsonl")
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    schedule = schedules.add(
        name="health",
        goal="project health",
        every_minutes=30,
        start_at=now - timedelta(minutes=5),
        include_tests=False,
        limit=2,
    )

    report = schedules.tick(task_queue=queue, now=now)

    assert report.due_count == 1
    assert report.enqueued_count == 1
    task = queue.load()[0]
    assert task.goal == "project health"
    assert not task.include_tests
    assert task.limit == 2
    updated = schedules.load()[0]
    assert updated.id == schedule.id
    assert updated.last_run_at is not None
    assert updated.next_run_at > updated.last_run_at


def test_tick_respects_limit(workspace: Path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    schedules = SchedulerStore(workspace / "schedules.jsonl")
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    for index in range(3):
        schedules.add(
            name=f"s{index}",
            goal=f"goal {index}",
            every_minutes=10,
            start_at=now - timedelta(minutes=1),
        )

    report = schedules.tick(task_queue=queue, now=now, limit=2)

    assert report.due_count == 2
    assert report.enqueued_count == 2
    assert len(queue.load()) == 2


def test_scheduler_skips_invalid_records_and_parses_string_booleans(workspace: Path):
    path = workspace / "schedules.jsonl"
    valid = {
        "name": "valid",
        "goal": "valid goal",
        "every_minutes": 5,
        "status": "active",
        "dry_run": "false",
        "include_tests": "false",
    }
    invalid = {
        "name": "invalid",
        "goal": "invalid goal",
        "every_minutes": 5,
        "status": "broken",
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

    schedules = SchedulerStore(path).load()

    assert len(schedules) == 1
    assert schedules[0].name == "valid"
    assert schedules[0].dry_run is False
    assert schedules[0].include_tests is False


def test_tick_reports_schedule_ids_in_due_order(workspace: Path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    schedules = SchedulerStore(workspace / "schedules.jsonl")
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    later = schedules.add(
        name="later",
        goal="later goal",
        every_minutes=10,
        start_at=now - timedelta(minutes=1),
    )
    earlier = schedules.add(
        name="earlier",
        goal="earlier goal",
        every_minutes=10,
        start_at=now - timedelta(minutes=5),
    )

    report = schedules.tick(task_queue=queue, now=now, limit=2)

    assert report.schedule_ids == (earlier.id, later.id)
    assert [task.goal for task in queue.load()] == ["earlier goal", "later goal"]
