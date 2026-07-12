"""Tests for app.priority_event_queue (daemon plan item 3.1)."""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.daemon import DaemonLoop
from app.priority_event_queue import (
    DaemonEvent,
    EventPriority,
    PriorityEventQueue,
    PriorityEventQueueClosed,
    PriorityEventQueueEmpty,
    coerce_priority,
)


def test_coerce_priority_accepts_name_enum_and_int():
    assert coerce_priority("urgent") is EventPriority.URGENT
    assert coerce_priority("SCHEDULED") is EventPriority.SCHEDULED
    assert coerce_priority(EventPriority.BACKGROUND) is EventPriority.BACKGROUND
    assert coerce_priority(2) is EventPriority.BACKGROUND


def test_coerce_priority_rejects_unknown():
    with pytest.raises(ValueError, match="unknown event priority"):
        coerce_priority("critical")
    with pytest.raises(ValueError, match="unknown event priority"):
        coerce_priority(99)
    with pytest.raises(ValueError, match="unknown event priority"):
        coerce_priority(True)


def test_put_rejects_empty_kind_and_closed_queue():
    q = PriorityEventQueue()
    with pytest.raises(ValueError, match="kind"):
        q.put("")
    q.close()
    with pytest.raises(PriorityEventQueueClosed):
        q.put("x", EventPriority.URGENT)


def test_get_nowait_empty_raises():
    q = PriorityEventQueue()
    with pytest.raises(PriorityEventQueueEmpty):
        q.get_nowait()


def test_priority_order_urgent_before_scheduled_before_background():
    q = PriorityEventQueue(aging_after=0)
    q.put("bg", "background", {"n": 1})
    q.put("sched", "scheduled", {"n": 2})
    q.put("urg", "urgent", {"n": 3})

    assert [e.kind for e in q.pop_batch()] == ["urg", "sched", "bg"]


def test_fifo_stable_within_same_priority():
    q = PriorityEventQueue(aging_after=0)
    q.put("a", EventPriority.SCHEDULED, {"i": 1})
    q.put("b", EventPriority.SCHEDULED, {"i": 2})
    q.put("c", EventPriority.SCHEDULED, {"i": 3})

    kinds = [e.kind for e in q.pop_batch()]
    assert kinds == ["a", "b", "c"]


def test_aging_serves_background_after_streak():
    q = PriorityEventQueue(aging_after=2)
    q.put("u1", EventPriority.URGENT)
    q.put("u2", EventPriority.URGENT)
    q.put("u3", EventPriority.URGENT)
    q.put("bg", EventPriority.BACKGROUND)

    assert q.get_nowait().kind == "u1"
    assert q.get_nowait().kind == "u2"
    # streak reached aging_after with background waiting → serve bg next
    assert q.get_nowait().kind == "bg"
    assert q.get_nowait().kind == "u3"


def test_aging_disabled_allows_background_starvation_until_end():
    q = PriorityEventQueue(aging_after=0)
    q.put("bg", EventPriority.BACKGROUND)
    q.put("u1", EventPriority.URGENT)
    q.put("u2", EventPriority.URGENT)

    assert [e.kind for e in q.pop_batch()] == ["u1", "u2", "bg"]


def test_invalid_aging_after_and_pop_batch_limit():
    with pytest.raises(ValueError, match="aging_after"):
        PriorityEventQueue(aging_after=-1)
    q = PriorityEventQueue()
    with pytest.raises(ValueError, match="max_items"):
        q.pop_batch(-1)


def test_daemon_event_to_dict_is_json_friendly():
    event = DaemonEvent(
        priority=EventPriority.URGENT,
        sequence=1,
        kind="approval",
        event_id="devent_test",
        payload={"path": "inbox.jsonl"},
        created_at="2026-07-12T00:00:00+00:00",
    )
    payload = event.to_dict()
    assert payload["priority"] == "urgent"
    assert payload["priority_value"] == 0
    assert payload["kind"] == "approval"
    assert payload["payload"] == {"path": "inbox.jsonl"}


def test_on_put_callback_error_is_isolated(caplog):
    def boom(_event: DaemonEvent) -> None:
        raise RuntimeError("wake failed")

    q = PriorityEventQueue(on_put=boom)
    with caplog.at_level(logging.ERROR, logger="app.priority_event_queue"):
        event = q.put("still-queued", EventPriority.URGENT)

    assert event.kind == "still-queued"
    assert len(q) == 1
    assert "on_put callback failed" in caplog.text
    assert q.get_nowait().event_id == event.event_id


def test_put_wakes_daemon_loop_immediately():
    seen: list[str] = []

    async def scenario() -> None:
        async def handle_wake(reasons: list[str]) -> None:
            seen.extend(reasons)
            daemon.request_stop()

        daemon = DaemonLoop(handle_wake)
        queue = PriorityEventQueue(
            on_put=lambda event: daemon.wake_threadsafe(f"event:{event.event_id}")
        )

        async def produce() -> None:
            await asyncio.sleep(0)
            queue.put("runtime_task", EventPriority.URGENT, {"goal": "x"})

        producer = asyncio.create_task(produce())
        await asyncio.wait_for(daemon.run(), timeout=2.0)
        await producer

    asyncio.run(scenario())
    assert len(seen) == 1
    assert seen[0].startswith("event:")


def test_async_get_waits_then_returns():
    async def scenario() -> None:
        q = PriorityEventQueue()

        async def produce() -> None:
            await asyncio.sleep(0)
            q.put("late", EventPriority.SCHEDULED)

        producer = asyncio.create_task(produce())
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        await producer
        assert event.kind == "late"

    asyncio.run(scenario())


def test_async_get_raises_when_closed_empty():
    async def scenario() -> None:
        q = PriorityEventQueue()
        q.close()
        with pytest.raises(PriorityEventQueueClosed):
            await asyncio.wait_for(q.get(), timeout=1.0)

    asyncio.run(scenario())


def test_close_unblocks_waiting_get():
    async def scenario() -> None:
        q = PriorityEventQueue()
        waiter = asyncio.create_task(q.get())
        await asyncio.sleep(0)
        assert not waiter.done()
        q.close()
        with pytest.raises(PriorityEventQueueClosed):
            await asyncio.wait_for(waiter, timeout=1.0)

    asyncio.run(scenario())


def test_repeated_close_is_safe():
    q = PriorityEventQueue()
    q.close()
    q.close()
    assert q.closed is True
