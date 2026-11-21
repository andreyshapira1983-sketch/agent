"""C16 — "nothing scheduled" must not look like "nothing due yet".

Found while measuring M61 (2026-08-14). Severing the scheduler wire left the
journal still writing `scheduler_tick` with zeros, and the control test
`test_budget_kill_switch.py::test_daemon_runs_normally_when_budget_available`
stayed green because it asserts the EVENT EXISTS, not that it did anything.
Reading the report showed why: `ScheduleTickReport` carries `due_count`,
`enqueued_count`, `task_ids` and `schedule_ids`, and no field says how many
schedules exist at all.

So one journal line answers three different questions identically:

  * the store holds NO schedules — which is the live workspace today,
    `data/runtime_schedules.jsonl` at 0 rows;
  * the store holds schedules and none is due yet;
  * (before M61's bite landed) the wire was severed.

That is the MIR-077 class — an invisible failure, three states collapsed into
one signal — and it lands on the operator's actual question: "why is my agent
doing nothing?" A daemon that never configured any work and a daemon patiently
waiting for the next window are the same line in the log.

These tests fail before the fix. The first is the distinction itself; the second
pins the honest value rather than merely "different", so a fix cannot pass by
emitting noise.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import agent_tick
from app.bootstrap import DEFAULT_RUNTIME_SCHEDULES_PATH
from core.scheduler import SchedulerStore


def _scheduler_events(workspace: Path) -> list[dict]:
    log_path = workspace / "logs" / "daemon_tick.jsonl"
    if not log_path.exists():
        return []
    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [e for e in events if e.get("event") == "scheduler_tick"]


def _tick_on_empty_store(workspace: Path) -> dict:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    agent_tick.run_tick(workspace, dry_run=True)
    events = _scheduler_events(workspace)
    assert len(events) == 1, f"expected exactly one scheduler_tick, got {events!r}"
    return events[0]


def _tick_with_a_schedule_not_yet_due(workspace: Path) -> dict:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "README.md").write_text("Project overview.", encoding="utf-8")
    SchedulerStore(workspace / DEFAULT_RUNTIME_SCHEDULES_PATH).add(
        name="later",
        goal="not yet",
        every_minutes=30,
        start_at=datetime.now(timezone.utc) + timedelta(hours=6),
        dry_run=True,
        include_tests=False,
        limit=1,
    )
    agent_tick.run_tick(workspace, dry_run=True)
    events = _scheduler_events(workspace)
    assert len(events) == 1, f"expected exactly one scheduler_tick, got {events!r}"
    return events[0]


def test_an_empty_store_and_a_waiting_schedule_are_different_journal_lines(tmp_path: Path):
    """The distinction the operator needs, asserted where the operator reads."""
    empty = _tick_on_empty_store(tmp_path / "empty")
    waiting = _tick_with_a_schedule_not_yet_due(tmp_path / "waiting")

    empty.pop("ts", None)
    waiting.pop("ts", None)

    assert empty != waiting, (
        "a daemon with NO schedules configured and a daemon waiting for the next "
        "window write the same journal line, so 'why is nothing happening?' has "
        f"no answer in the log: {empty!r}"
    )


def test_the_journal_says_how_many_schedules_exist(tmp_path: Path):
    """Different is not enough — the value must be the honest count.

    Without this, a fix could satisfy the test above by emitting a timestamp or
    a random field and still leave the operator without the number that answers
    the question.
    """
    empty = _tick_on_empty_store(tmp_path / "empty")
    waiting = _tick_with_a_schedule_not_yet_due(tmp_path / "waiting")

    assert empty.get("total_count") == 0, (
        "an empty schedule store must report 0 schedules, not silence: "
        f"{empty!r}"
    )
    assert waiting.get("total_count") == 1, (
        f"a store holding one waiting schedule must report 1: {waiting!r}"
    )
    # The pre-existing fields keep their meaning: neither tick had work to do.
    assert empty.get("due_count") == 0
    assert waiting.get("due_count") == 0
