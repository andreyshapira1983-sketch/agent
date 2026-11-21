"""Claiming a task must be exclusive — two consumers cannot both get it.

Reproduced 2026-08-04 with two real processes against one queue file: both
claimed the same task, `attempts` went to 2 on the claim alone, and the second
`owner_pid` overwrote the first, so the row no longer said who was running it.
Both consumers then went off to do the same work.

`mark_running` never checked the task was still `pending` — `_update_one` only
raises `KeyError` for a missing id — so nothing anywhere refused the second
claim. `agent_tick.py` even carried `except Exception: continue  # another
process already claimed it`, guarding against an exception that was never
raised — that guard was removed with this fix and is kept only as a recorded
mistake. And the two operator-facing entry points, `:task-run` and
`:schedule-tick --run` (`app/task_scheduler_cli.py:179`, `:385`), drain the
queue without the single-instance lock that `agent_tick` takes.

The window is not theoretical: `run_task_queue` reads the pending list ONCE and
then works through it, so a task in the tail is claimed however many minutes
after it was listed as the earlier tasks took to run.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.task_queue import TaskAlreadyClaimed, TaskQueueStore


@pytest.mark.parametrize("finish", ["mark_done", "cancel"])
def test_a_finished_task_cannot_be_claimed_back_into_running(tmp_path: Path, finish: str):
    """Claiming must not resurrect work that already reached a resting state."""
    store = TaskQueueStore(tmp_path / "tasks.jsonl")
    task = store.add(goal="already over")
    store.mark_running(task.id)
    getattr(store, finish)(task.id)

    with pytest.raises(TaskAlreadyClaimed):
        store.mark_running(task.id)


def test_a_missing_task_still_raises_key_error(tmp_path: Path):
    """The two failures stay distinguishable: gone is not the same as taken."""
    store = TaskQueueStore(tmp_path / "tasks.jsonl")

    with pytest.raises(KeyError):
        store.mark_running("rtask_does_not_exist")
