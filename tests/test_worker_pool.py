"""Tests for app.worker_pool (daemon plan items 3.2–3.3)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path

import pytest

from app.priority_event_queue import (
    EventPriority,
    PriorityEventQueue,
    PriorityEventQueueClosed,
)
from app.worker_pool import (
    DEFAULT_MAX_WORKERS,
    DEFAULT_TASK_TIMEOUT,
    InFlightCheckpointStore,
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


def _checkpoint_store(tmp_path: Path) -> InFlightCheckpointStore:
    fixed = datetime(2026, 7, 16, 12, 30, tzinfo=timezone.utc)
    return InFlightCheckpointStore(
        tmp_path / "in_flight.jsonl",
        now=lambda: fixed,
    )


@pytest.mark.asyncio
async def test_checkpoint_is_durable_before_handler_and_removed_on_success(tmp_path):
    q = PriorityEventQueue()
    store = _checkpoint_store(tmp_path)
    observed: list[dict] = []

    async def handler(event) -> None:
        observed.extend(store.load())

    pool = WorkerPool(q, handler, max_workers=1, checkpoint_store=store)
    run_task = asyncio.create_task(pool.run())
    await asyncio.sleep(0)
    event = q.put("success", payload={"task_id": "rtask_1"})
    q.close()
    await asyncio.wait_for(run_task, timeout=2.0)

    assert observed[0]["event_id"] == event.event_id
    assert observed[0]["checkpointed_at"] == "2026-07-16T12:30:00+00:00"
    assert observed[0]["event"]["payload"] == {"task_id": "rtask_1"}
    assert store.load() == []


@pytest.mark.asyncio
async def test_checkpoint_removed_after_handler_error(tmp_path):
    q = PriorityEventQueue()
    store = _checkpoint_store(tmp_path)

    async def handler(event) -> None:
        assert store.load()
        raise RuntimeError("boom")

    pool = WorkerPool(q, handler, max_workers=1, checkpoint_store=store)
    run_task = asyncio.create_task(pool.run())
    await asyncio.sleep(0)
    q.put("error")
    q.close()
    await asyncio.wait_for(run_task, timeout=2.0)

    assert pool.error_count == 1
    assert store.load() == []


@pytest.mark.asyncio
async def test_checkpoint_removed_after_timeout(tmp_path):
    q = PriorityEventQueue()
    store = _checkpoint_store(tmp_path)

    async def handler(event) -> None:
        await asyncio.Event().wait()

    pool = WorkerPool(
        q,
        handler,
        max_workers=1,
        task_timeout=0.01,
        checkpoint_store=store,
    )
    run_task = asyncio.create_task(pool.run())
    await asyncio.sleep(0)
    q.put("timeout")
    q.close()
    await asyncio.wait_for(run_task, timeout=2.0)

    assert pool.timeout_count == 1
    assert store.load() == []


@pytest.mark.asyncio
async def test_checkpoint_removed_after_explicit_handler_cancellation(tmp_path):
    q = PriorityEventQueue()
    store = _checkpoint_store(tmp_path)
    started = asyncio.Event()

    async def handler(event) -> None:
        started.set()
        await asyncio.Event().wait()

    pool = WorkerPool(q, handler, max_workers=1, task_timeout=None, checkpoint_store=store)
    run_task = asyncio.create_task(pool.run())
    await asyncio.sleep(0)
    q.put("cancel")
    q.close()
    await asyncio.wait_for(started.wait(), timeout=1.0)
    next(iter(pool._in_flight)).cancel()
    await asyncio.wait_for(run_task, timeout=2.0)

    assert pool.cancelled_count == 1
    assert store.load() == []


@pytest.mark.asyncio
async def test_checkpoint_removed_after_shutdown_cancellation(tmp_path):
    q = PriorityEventQueue()
    store = _checkpoint_store(tmp_path)
    started = asyncio.Event()

    async def handler(event) -> None:
        started.set()
        await asyncio.Event().wait()

    pool = WorkerPool(
        q,
        handler,
        max_workers=1,
        drain_timeout=0,
        task_timeout=None,
        checkpoint_store=store,
    )
    run_task = asyncio.create_task(pool.run())
    await asyncio.sleep(0)
    q.put("shutdown")
    await asyncio.wait_for(started.wait(), timeout=1.0)
    await pool.shutdown(drain_timeout=0)
    await asyncio.wait_for(run_task, timeout=2.0)

    assert store.load() == []


@pytest.mark.asyncio
async def test_concurrent_handlers_keep_independent_checkpoints(tmp_path):
    q = PriorityEventQueue()
    store = _checkpoint_store(tmp_path)
    both_started = asyncio.Event()
    release = asyncio.Event()
    started = 0

    async def handler(event) -> None:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await release.wait()

    pool = WorkerPool(q, handler, max_workers=2, checkpoint_store=store)
    run_task = asyncio.create_task(pool.run())
    await asyncio.sleep(0)
    first = q.put("first")
    second = q.put("second")
    q.close()
    await asyncio.wait_for(both_started.wait(), timeout=1.0)

    assert {row["event_id"] for row in store.load()} == {
        first.event_id,
        second.event_id,
    }
    release.set()
    await asyncio.wait_for(run_task, timeout=2.0)
    assert store.load() == []


def test_checkpoint_atomic_failure_preserves_previous_file(tmp_path, monkeypatch):
    store = _checkpoint_store(tmp_path)
    q = PriorityEventQueue()
    first = q.put("first")
    second = q.put("second")
    store.checkpoint(first, worker_id=0)
    original_replace = Path.replace

    def fail_replace(path, target):
        if Path(target) == store.path:
            raise OSError("simulated replace failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        store.checkpoint(second, worker_id=1)

    assert [row["event_id"] for row in store.load()] == [first.event_id]


def test_checkpoint_cleanup_is_idempotent_and_preserves_other_work(tmp_path):
    store = _checkpoint_store(tmp_path)
    q = PriorityEventQueue()
    first = q.put("first")
    second = q.put("second")
    store.checkpoint(first, worker_id=0)
    store.checkpoint(second, worker_id=1)

    assert store.remove(first.event_id) is True
    assert store.remove(first.event_id) is False
    assert [row["event_id"] for row in store.load()] == [second.event_id]


@pytest.mark.asyncio
async def test_checkpoint_write_failure_prevents_handler_execution(tmp_path, monkeypatch):
    q = PriorityEventQueue()
    store = _checkpoint_store(tmp_path)
    called = False

    def fail_checkpoint(event, *, worker_id):
        raise OSError("disk unavailable")

    monkeypatch.setattr(store, "checkpoint", fail_checkpoint)

    async def handler(event) -> None:
        nonlocal called
        called = True

    pool = WorkerPool(q, handler, max_workers=1, checkpoint_store=store)
    run_task = asyncio.create_task(pool.run())
    await asyncio.sleep(0)
    q.put("must-not-run")
    q.close()
    await asyncio.wait_for(run_task, timeout=2.0)

    assert called is False
    assert pool.error_count == 1
    assert pool.processed_count == 0


def test_crash_left_checkpoint_is_json_safe_and_reloadable(tmp_path):
    store = _checkpoint_store(tmp_path)
    q = PriorityEventQueue()
    event = q.put(
        "interrupted",
        EventPriority.BACKGROUND,
        payload={"when": datetime(2026, 7, 16, tzinfo=timezone.utc)},
        dedup_key="runtime-task:42",
    )

    store.checkpoint(event, worker_id=3)  # Simulate crash: no terminal cleanup.
    reloaded = InFlightCheckpointStore(store.path).load()

    assert reloaded == [
        {
            "format": InFlightCheckpointStore.FORMAT,
            "event_id": event.event_id,
            "worker_id": 3,
            "checkpointed_at": "2026-07-16T12:30:00+00:00",
            "event": {
                **event.to_dict(),
                "payload": {"when": "2026-07-16 00:00:00+00:00"},
            },
        }
    ]


async def _noop(event) -> None:
    return None
