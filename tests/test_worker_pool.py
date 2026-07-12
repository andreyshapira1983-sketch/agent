"""Tests for app.worker_pool (daemon plan items 3.2–3.3)."""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.priority_event_queue import (
    EventPriority,
    PriorityEventQueue,
    PriorityEventQueueClosed,
)
from app.worker_pool import (
    DEFAULT_MAX_WORKERS,
    DEFAULT_TASK_TIMEOUT,
    WorkerPool,
    WorkerPoolError,
)


def test_default_max_workers_and_task_timeout():
    q = PriorityEventQueue()
    pool = WorkerPool(q, handler=_noop)
    assert pool.max_workers == DEFAULT_MAX_WORKERS == 2
    assert pool.task_timeout == DEFAULT_TASK_TIMEOUT == 60.0


def test_rejects_invalid_max_workers_drain_and_task_timeout():
    q = PriorityEventQueue()
    with pytest.raises(ValueError, match="max_workers"):
        WorkerPool(q, handler=_noop, max_workers=0)
    with pytest.raises(ValueError, match="drain_timeout"):
        WorkerPool(q, handler=_noop, drain_timeout=-1)
    with pytest.raises(ValueError, match="task_timeout"):
        WorkerPool(q, handler=_noop, task_timeout=0)
    with pytest.raises(ValueError, match="task_timeout"):
        WorkerPool(q, handler=_noop, task_timeout=-1)


@pytest.mark.asyncio
async def test_processes_events_and_respects_concurrency():
    q = PriorityEventQueue()
    current = 0
    peak = 0
    lock = asyncio.Lock()
    seen: list[str] = []

    async def handler(event) -> None:
        nonlocal current, peak
        async with lock:
            current += 1
            peak = max(peak, current)
        await asyncio.sleep(0)
        async with lock:
            current -= 1
            seen.append(event.kind)

    pool = WorkerPool(q, handler, max_workers=2)
    run_task = asyncio.create_task(pool.run())
    await asyncio.sleep(0)
    for i in range(5):
        q.put(f"e{i}", EventPriority.SCHEDULED)
    q.close()
    await asyncio.wait_for(run_task, timeout=2.0)

    assert sorted(seen) == [f"e{i}" for i in range(5)]
    assert peak <= 2
    assert pool.processed_count == 5
    assert pool.error_count == 0
    assert pool.timeout_count == 0


@pytest.mark.asyncio
async def test_handler_error_does_not_stop_pool(caplog):
    q = PriorityEventQueue()
    seen: list[str] = []

    async def handler(event) -> None:
        if event.kind == "bad":
            raise RuntimeError("boom")
        seen.append(event.kind)

    pool = WorkerPool(q, handler, max_workers=1)
    run_task = asyncio.create_task(pool.run())
    await asyncio.sleep(0)
    q.put("bad", EventPriority.URGENT)
    q.put("good", EventPriority.SCHEDULED)
    q.close()
    with caplog.at_level(logging.ERROR):
        await asyncio.wait_for(run_task, timeout=2.0)

    assert seen == ["good"]
    assert pool.error_count == 1
    assert pool.processed_count == 1
    assert any("failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_shutdown_stops_accepting_and_is_idempotent():
    q = PriorityEventQueue()
    started = asyncio.Event()

    async def handler(event) -> None:
        started.set()
        await asyncio.Event().wait()

    # Disable per-task timeout so shutdown cancel is what ends the parked task.
    pool = WorkerPool(
        q, handler, max_workers=1, drain_timeout=0.05, task_timeout=None
    )
    run_task = asyncio.create_task(pool.run())
    await asyncio.sleep(0)
    q.put("slow", EventPriority.SCHEDULED)
    await asyncio.wait_for(started.wait(), timeout=1.0)

    await pool.shutdown()
    with pytest.raises(PriorityEventQueueClosed):
        q.put("after", EventPriority.URGENT)

    assert pool.shutting_down is True
    assert pool.active_workers == 0
    await pool.shutdown()
    with pytest.raises(WorkerPoolError, match="shutdown"):
        pool.start()
    await asyncio.wait_for(run_task, timeout=2.0)
    assert pool.processed_count == 0  # cancel ≠ success


@pytest.mark.asyncio
async def test_shutdown_drains_short_work_before_exit():
    q = PriorityEventQueue()
    done: list[str] = []

    async def handler(event) -> None:
        await asyncio.sleep(0)
        done.append(event.kind)

    pool = WorkerPool(q, handler, max_workers=2, drain_timeout=2.0)
    run_task = asyncio.create_task(pool.run())
    await asyncio.sleep(0)
    q.put("a", EventPriority.SCHEDULED)
    q.put("b", EventPriority.SCHEDULED)
    await pool.shutdown(drain_timeout=2.0)
    await asyncio.wait_for(run_task, timeout=2.0)
    assert sorted(done) == ["a", "b"]
    assert pool.processed_count == 2


@pytest.mark.asyncio
async def test_run_cancellation_triggers_shutdown():
    q = PriorityEventQueue()
    gate = asyncio.Event()

    async def handler(event) -> None:
        gate.set()
        await asyncio.Event().wait()

    pool = WorkerPool(
        q, handler, max_workers=1, drain_timeout=0.05, task_timeout=None
    )
    run_task = asyncio.create_task(pool.run())
    await asyncio.sleep(0)
    q.put("park", EventPriority.BACKGROUND)
    await asyncio.wait_for(gate.wait(), timeout=1.0)
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert pool.shutting_down is True
    assert pool.processed_count == 0


@pytest.mark.asyncio
async def test_task_timeout_then_pool_continues_with_next_event(caplog):
    q = PriorityEventQueue()
    seen: list[str] = []

    async def handler(event) -> None:
        if event.kind == "hung":
            await asyncio.Event().wait()
        seen.append(event.kind)

    pool = WorkerPool(q, handler, max_workers=1, task_timeout=0.05)
    run_task = asyncio.create_task(pool.run())
    await asyncio.sleep(0)
    q.put("hung", EventPriority.URGENT)
    q.put("ok", EventPriority.SCHEDULED)
    q.close()
    with caplog.at_level(logging.WARNING):
        await asyncio.wait_for(run_task, timeout=2.0)

    assert seen == ["ok"]
    assert pool.timeout_count == 1
    assert pool.processed_count == 1
    assert pool.metrics()["timeouts"] == 1
    assert pool.metrics()["processed"] == 1
    assert any(
        "task timed out" in r.message and "reason=task_timeout" in r.message
        for r in caplog.records
    )

@pytest.mark.asyncio
async def test_task_timeout_none_allows_long_handler_until_shutdown():
    q = PriorityEventQueue()
    started = asyncio.Event()

    async def handler(event) -> None:
        started.set()
        await asyncio.Event().wait()

    pool = WorkerPool(
        q, handler, max_workers=1, task_timeout=None, drain_timeout=0.05
    )
    run_task = asyncio.create_task(pool.run())
    await asyncio.sleep(0)
    q.put("slow", EventPriority.SCHEDULED)
    await asyncio.wait_for(started.wait(), timeout=1.0)
    await asyncio.sleep(0.08)  # would have timed out if default 0.05 applied
    assert pool.timeout_count == 0
    assert pool.processed_count == 0
    await pool.shutdown()
    await asyncio.wait_for(run_task, timeout=2.0)


async def _noop(event) -> None:
    return None
