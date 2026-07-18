"""Regression: a re-queued failed task must back off, not hot-retry.

OFM-010 / CORE-07: ``TaskQueueStore.mark_failed`` re-queued a non-exhausted task
to ``pending`` WITHOUT advancing ``run_after``. ``pending()`` serves any task
whose ``run_after <= now``, so a deterministically failing task with
``max_attempts > 1`` was immediately eligible again on the next tick — a hot
retry loop with no backoff.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.task_queue import TaskQueueStore, _parse_iso


def _run_and_fail(store: TaskQueueStore, *, max_attempts: int):
    task = store.add(goal="deterministic failure", max_attempts=max_attempts)
    store.mark_running(task.id)  # bumps attempts
    return store.mark_failed(task.id, error="boom")


def test_requeued_failure_backs_off(tmp_path):
    store = TaskQueueStore(tmp_path / "q.jsonl")
    failed = _run_and_fail(store, max_attempts=2)  # attempts=1 < 2 -> re-pend
    assert failed.status == "pending"

    now = datetime.now(timezone.utc)
    # run_after must be pushed into the future (backoff), not left at ~now/past.
    assert _parse_iso(failed.run_after) > now + timedelta(seconds=1), (
        "re-queued task should back off into the future"
    )
    # Not eligible right now (still backing off)...
    assert failed.id not in [t.id for t in store.pending(now=now)]
    # ...but eligible once the backoff window elapses.
    later = now + timedelta(hours=2)
    assert failed.id in [t.id for t in store.pending(now=later)]


def test_exhausted_task_is_failed_not_requeued(tmp_path):
    store = TaskQueueStore(tmp_path / "q.jsonl")
    failed = _run_and_fail(store, max_attempts=1)  # attempts=1 >= 1 -> terminal
    assert failed.status == "failed"
