"""Tests for app.worker_pool (daemon plan item 3.2)."""
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
    WorkerPool,
    WorkerPoolError,
)


def test_default_max_workers_is_two():
    q = PriorityEventQueue()
    pool = WorkerPool(q, handler=_noop)
    assert pool.max_workers == DEFAULT_MAX_WORKERS == 2


def test_rejects_invalid_max_workers_and_drain():
    q = PriorityEventQueue()
    with pytest.raises(ValueError, match="max_workers"):
        WorkerPool(q, handler=_noop, max_workers=0)
    with pytest.raises(ValueError, match="drain_timeout"):
        WorkerPool(q, handler=_noop, drain_timeout=-1)


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
    await asyncio.sleep(0)  # workers blocked on get
    for i in range(5):
        q.put(f"e{i}", EventPriority.SCHEDULED)
    q.close()
    await asyncio.wait_for(run_task, timeout=2.0)

    assert sorted(seen) == [f"e{i}" for i in range(5)]
    assert peak <= 2
    assert pool.processed_count == 5
    assert pool.error_count == 0


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
        await asyncio.Event().wait()  # park until cancelled

    pool = WorkerPool(q, handler, max_workers=1, drain_timeout=0.05)
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
    # run() should finish after shutdown cancelled workers
    await asyncio.wait_for(run_task, timeout=2.0)


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

    pool = WorkerPool(q, handler, max_workers=1, drain_timeout=0.05)
    run_task = asyncio.create_task(pool.run())
    await asyncio.sleep(0)
    q.put("park", EventPriority.BACKGROUND)
    await asyncio.wait_for(gate.wait(), timeout=1.0)
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert pool.shutting_down is True


async def _noop(event) -> None:
    return None
