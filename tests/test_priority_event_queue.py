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
    assert "dedup_key" not in payload


def test_same_dedup_key_returns_first_without_replacing_or_promoting():
    q = PriorityEventQueue(aging_after=0)
    first = q.put(
        "first",
        EventPriority.SCHEDULED,
        {"version": 1},
        event_id="devent_first",
        dedup_key="task:1",
    )
    sibling = q.put("sibling", EventPriority.SCHEDULED)

    duplicate = q.put(
        "replacement",
        EventPriority.URGENT,
        {"version": 2},
        event_id="devent_duplicate",
        dedup_key="task:1",
    )
    after_duplicate = q.put("after-duplicate", EventPriority.SCHEDULED)

    assert duplicate is first
    assert len(q) == 3
    assert first.kind == "first"
    assert first.payload == {"version": 1}
    assert first.priority is EventPriority.SCHEDULED
    assert first.event_id == "devent_first"
    assert first.to_dict()["dedup_key"] == "task:1"
    assert sibling.sequence == first.sequence + 1
    assert after_duplicate.sequence == sibling.sequence + 1
    assert [event.kind for event in q.pop_batch()] == [
        "first",
        "sibling",
        "after-duplicate",
    ]


def test_different_dedup_keys_are_independent():
    q = PriorityEventQueue()
    first = q.put("first", dedup_key="task:1")
    second = q.put("second", dedup_key="task:2")

    assert first is not second
    assert len(q) == 2
    assert {event.dedup_key for event in q.pop_batch()} == {"task:1", "task:2"}


def test_missing_dedup_key_preserves_duplicate_events():
    q = PriorityEventQueue()
    first = q.put("same", payload={"value": 1})
    second = q.put("same", payload={"value": 1})

    assert first is not second
    assert second.sequence == first.sequence + 1
    assert len(q) == 2


def test_dedup_key_can_be_reused_after_get_nowait():
    q = PriorityEventQueue()
    first = q.put("first", dedup_key="task:1")
    assert q.get_nowait() is first

    replacement = q.put("replacement", dedup_key="task:1")

    assert replacement is not first
    assert replacement.sequence == first.sequence + 1


def test_pop_batch_releases_each_dedup_key():
    q = PriorityEventQueue()
    first = q.put("first", EventPriority.URGENT, dedup_key="task:1")
    second = q.put("second", EventPriority.SCHEDULED, dedup_key="task:2")

    assert q.pop_batch() == [first, second]
    assert q.put("first-again", dedup_key="task:1") is not first
    assert q.put("second-again", dedup_key="task:2") is not second


def test_background_aging_pop_releases_dedup_key():
    q = PriorityEventQueue(aging_after=1)
    q.put("urgent", EventPriority.URGENT)
    background = q.put(
        "background",
        EventPriority.BACKGROUND,
        dedup_key="maintenance",
    )

    assert q.get_nowait().kind == "urgent"
    assert q.get_nowait() is background
    assert q.put("background-again", dedup_key="maintenance") is not background


def test_duplicate_calls_on_put_only_once():
    callbacks: list[DaemonEvent] = []
    q = PriorityEventQueue(on_put=callbacks.append)

    first = q.put("first", dedup_key="task:1")
    duplicate = q.put("duplicate", dedup_key="task:1")

    assert duplicate is first
    assert callbacks == [first]


def test_empty_dedup_key_is_rejected():
    q = PriorityEventQueue()
    for key in ("", "   ", "\t\n"):
        with pytest.raises(ValueError, match="dedup_key"):
            q.put("event", dedup_key=key)

    with pytest.raises(ValueError, match="dedup_key"):
        DaemonEvent(
            priority=EventPriority.SCHEDULED,
            sequence=1,
            kind="event",
            dedup_key=" ",
        )


def test_closed_queue_rejects_duplicate_without_consuming_pending_event():
    q = PriorityEventQueue()
    first = q.put("first", dedup_key="task:1")
    q.close()

    with pytest.raises(PriorityEventQueueClosed):
        q.put("duplicate", dedup_key="task:1")

    assert len(q) == 1
    assert q.get_nowait() is first


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


def test_async_get_releases_dedup_key():
    async def scenario() -> None:
        q = PriorityEventQueue()
        first = q.put("first", dedup_key="task:1")

        assert await q.get() is first
        replacement = q.put("replacement", dedup_key="task:1")
        assert replacement is not first

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
