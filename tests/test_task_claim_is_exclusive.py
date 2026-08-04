"""Claiming a task must be exclusive — two consumers cannot both get it.

Reproduced 2026-08-04 with two real processes against one queue file: both
claimed the same task, `attempts` went to 2 on the claim alone, and the second
`owner_pid` overwrote the first, so the row no longer said who was running it.
Both consumers then went off to do the same work.

`mark_running` never checked the task was still `pending` — `_update_one` only
raises `KeyError` for a missing id — so nothing anywhere refused the second
claim. `agent_tick.py:929` even carries `except Exception: continue  # another
process already claimed it`, guarding against an exception that was never
raised. And the two operator-facing entry points, `:task-run` and
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


def test_a_second_claim_of_a_running_task_is_refused(tmp_path: Path):
    store = TaskQueueStore(tmp_path / "tasks.jsonl")
    task = store.add(goal="only one of you should run me", max_attempts=3)

    first = store.mark_running(task.id)
    assert first.status == "running"

    with pytest.raises(TaskAlreadyClaimed):
        store.mark_running(task.id)


def test_a_refused_claim_changes_nothing_about_the_task(tmp_path: Path):
    """The loser of the race must not touch the winner's row."""
    store = TaskQueueStore(tmp_path / "tasks.jsonl")
    task = store.add(goal="repair", max_attempts=3)
    winner = store.mark_running(task.id, owner_pid=4242, owner_host="winner")

    with pytest.raises(TaskAlreadyClaimed):
        store.mark_running(task.id, owner_pid=777, owner_host="loser")

    after = store.get(task.id)
    assert after.attempts == winner.attempts == 1, "the claim burned an attempt"
    assert after.owner_pid == 4242, "the loser overwrote the running owner"
    assert after.owner_host == "winner"


@pytest.mark.parametrize("finish", ["mark_done", "cancel"])
def test_a_finished_task_cannot_be_claimed_back_into_running(tmp_path: Path, finish: str):
    """Claiming must not resurrect work that already reached a resting state."""
    store = TaskQueueStore(tmp_path / "tasks.jsonl")
    task = store.add(goal="already over")
    store.mark_running(task.id)
    getattr(store, finish)(task.id)

    with pytest.raises(TaskAlreadyClaimed):
        store.mark_running(task.id)


def test_the_refusal_names_the_status_that_blocked_it(tmp_path: Path):
    """A consumer that logs the error should be able to say what happened."""
    store = TaskQueueStore(tmp_path / "tasks.jsonl")
    task = store.add(goal="repair")
    store.mark_running(task.id)

    with pytest.raises(TaskAlreadyClaimed, match="running"):
        store.mark_running(task.id)


def test_claiming_a_pending_task_still_works(tmp_path: Path):
    store = TaskQueueStore(tmp_path / "tasks.jsonl")
    task = store.add(goal="repair", max_attempts=2)

    claimed = store.mark_running(task.id)

    assert claimed.status == "running"
    assert claimed.attempts == 1


def test_a_requeued_task_can_be_claimed_again(tmp_path: Path):
    """The exclusion is per-claim, not per-lifetime: a retry must still run."""
    store = TaskQueueStore(tmp_path / "tasks.jsonl")
    task = store.add(goal="flaky", max_attempts=3)
    store.mark_running(task.id)
    store.mark_failed(task.id, error="first failure")

    second = store.mark_running(task.id)

    assert second.status == "running"
    assert second.attempts == 2


def test_a_missing_task_still_raises_key_error(tmp_path: Path):
    """The two failures stay distinguishable: gone is not the same as taken."""
    store = TaskQueueStore(tmp_path / "tasks.jsonl")

    with pytest.raises(KeyError):
        store.mark_running("rtask_does_not_exist")
