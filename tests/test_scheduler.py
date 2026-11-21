from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.scheduler import SchedulerStore


def test_due_returns_only_active_due_schedules(workspace: Path):
    """Each filter alone excludes: a future active schedule and a paused one already due."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = SchedulerStore(workspace / "schedules.jsonl")
    due = store.add(
        name="due",
        goal="due goal",
        every_minutes=10,
        start_at=now - timedelta(minutes=1),
    )
    store.add(
        name="future",
        goal="future goal",
        every_minutes=10,
        start_at=now + timedelta(minutes=1),
    )
    paused = store.add(
        name="paused",
        goal="paused goal",
        every_minutes=10,
        start_at=now - timedelta(minutes=1),
    )
    store.pause(paused.id)

    due_now = store.due(now=now)

    assert [schedule.id for schedule in due_now] == [due.id]


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
