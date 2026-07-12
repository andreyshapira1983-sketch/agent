"""In-memory priority event queue for the daemon dispatcher (plan item 3.1).

This is the first dispatcher building block after the event sources (2.1–2.3).
Producers (timer ticks, file-watcher batches, RuntimeTask wake-ups, future
control commands) enqueue typed :class:`DaemonEvent` values; a later worker
pool (3.2) will consume them. This module does **not** execute work and does
**not** own :class:`~app.daemon.DaemonLoop`.

Priorities
----------
``urgent > scheduled > background``. Within one priority band, events are
strictly FIFO (stable insertion order via a monotonic sequence number).

Anti-starvation policy
----------------------
Strict priority alone can starve ``background`` forever under a continuous
stream of higher-priority events. After ``aging_after`` consecutive pops that
served ``urgent`` or ``scheduled``, if any ``background`` event is waiting,
the next :meth:`PriorityEventQueue.pop` / :meth:`get_nowait` serves the oldest
background event instead and resets the aging counter. Set ``aging_after`` to
``0`` to disable aging (pure strict priority — starvation possible; documented).

``agent_tick.py`` is untouched; nothing here replaces the single-shot path.
"""
from __future__ import annotations

import asyncio
import heapq
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Callable, Mapping, Optional

from core.ids import new_id

logger = logging.getLogger(__name__)

# Default: after this many consecutive higher-priority pops, prefer one
# waiting background event if present.
DEFAULT_AGING_AFTER = 8


class EventPriority(IntEnum):
    """Dispatch priority bands (lower IntEnum value = served first)."""

    URGENT = 0
    SCHEDULED = 1
    BACKGROUND = 2


_PRIORITY_BY_NAME: dict[str, EventPriority] = {
    "urgent": EventPriority.URGENT,
    "scheduled": EventPriority.SCHEDULED,
    "background": EventPriority.BACKGROUND,
}


def coerce_priority(value: EventPriority | str | int) -> EventPriority:
    """Parse a priority from enum, name, or int; raise ``ValueError`` if bad."""
    if isinstance(value, EventPriority):
        return value
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _PRIORITY_BY_NAME:
            return _PRIORITY_BY_NAME[key]
        raise ValueError(f"unknown event priority: {value!r}")
    if isinstance(value, int) and not isinstance(value, bool):
        try:
            return EventPriority(value)
        except ValueError as exc:
            raise ValueError(f"unknown event priority: {value!r}") from exc
    raise ValueError(f"unknown event priority: {value!r}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, order=True)
class DaemonEvent:
    """One typed dispatcher event.

    Ordering fields (``priority``, ``sequence``) come first so a heap of
    events sorts by priority then stable FIFO without a custom key function.
    Payload and ids are excluded from comparisons.
    """

    priority: EventPriority
    sequence: int
    kind: str = field(compare=False)
    event_id: str = field(compare=False, default_factory=lambda: new_id("devent"))
    payload: Mapping[str, Any] = field(compare=False, default_factory=dict)
    created_at: str = field(compare=False, default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "priority": self.priority.name.lower(),
            "priority_value": int(self.priority),
            "sequence": self.sequence,
            "payload": dict(self.payload),
            "created_at": self.created_at,
        }


class PriorityEventQueueEmpty(LookupError):
    """Raised by non-blocking pop when the queue has no events."""


class PriorityEventQueueClosed(RuntimeError):
    """Raised when enqueue is attempted after :meth:`PriorityEventQueue.close`."""


PutCallback = Callable[[DaemonEvent], None]


class PriorityEventQueue:
    """Thread-hostile, asyncio-friendly priority queue (one loop owner).

    Parameters
    ----------
    aging_after:
        Anti-starvation threshold (see module docstring). ``0`` disables aging.
    on_put:
        Optional sync callback invoked after a successful put (e.g. to call
        ``DaemonLoop.wake`` / ``wake_threadsafe``). Failures are logged and
        isolated so a wake-hook bug cannot drop an already-queued event.
    """

    def __init__(
        self,
        *,
        aging_after: int = DEFAULT_AGING_AFTER,
        on_put: Optional[PutCallback] = None,
    ) -> None:
        if aging_after < 0:
            raise ValueError("aging_after must be >= 0")
        self._aging_after = aging_after
        self._on_put = on_put
        self._heap: list[DaemonEvent] = []
        self._sequence = 0
        self._high_priority_streak = 0
        self._closed = False
        self._not_empty = asyncio.Event()

    @property
    def closed(self) -> bool:
        return self._closed

    def __len__(self) -> int:
        return len(self._heap)

    def qsize(self) -> int:
        return len(self._heap)

    def close(self) -> None:
        """Refuse further puts; wake any :meth:`get` waiters so they can exit."""
        self._closed = True
        self._not_empty.set()

    def put(
        self,
        kind: str,
        priority: EventPriority | str | int = EventPriority.SCHEDULED,
        payload: Mapping[str, Any] | None = None,
        *,
        event_id: str | None = None,
    ) -> DaemonEvent:
        """Enqueue one event. Raises :class:`PriorityEventQueueClosed` if closed."""
        if self._closed:
            raise PriorityEventQueueClosed("priority event queue is closed")
        kind_s = str(kind or "").strip()
        if not kind_s:
            raise ValueError("event kind must be a non-empty string")
        band = coerce_priority(priority)
        self._sequence += 1
        event = DaemonEvent(
            priority=band,
            sequence=self._sequence,
            kind=kind_s,
            event_id=event_id or new_id("devent"),
            payload=dict(payload or {}),
        )
        heapq.heappush(self._heap, event)
        self._not_empty.set()
        if self._on_put is not None:
            try:
                self._on_put(event)
            except Exception:  # noqa: BLE001 - wake hook must not drop queued work
                logger.exception(
                    "priority event queue on_put callback failed for %s",
                    event.event_id,
                )
        return event

    def get_nowait(self) -> DaemonEvent:
        """Pop the next event or raise :class:`PriorityEventQueueEmpty`."""
        event = self._pop_next()
        if event is None:
            raise PriorityEventQueueEmpty("priority event queue is empty")
        return event

    def pop_batch(self, max_items: int | None = None) -> list[DaemonEvent]:
        """Pop up to ``max_items`` events in dispatch order (empty list if none)."""
        if max_items is not None and max_items < 0:
            raise ValueError("max_items must be >= 0 or None")
        out: list[DaemonEvent] = []
        limit = len(self._heap) if max_items is None else max_items
        while len(out) < limit:
            event = self._pop_next()
            if event is None:
                break
            out.append(event)
        return out

    async def get(self) -> DaemonEvent:
        """Wait until an event is available, then pop it.

        If the queue is closed and empty, raises :class:`PriorityEventQueueClosed`.
        """
        while True:
            event = self._pop_next()
            if event is not None:
                return event
            if self._closed:
                raise PriorityEventQueueClosed("priority event queue is closed")
            self._not_empty.clear()
            # Re-check after clearing to avoid a lost wake between pop and wait.
            if self._heap:
                self._not_empty.set()
                continue
            if self._closed:
                raise PriorityEventQueueClosed("priority event queue is closed")
            await self._not_empty.wait()

    def _pop_next(self) -> DaemonEvent | None:
        if not self._heap:
            self._not_empty.clear()
            return None

        if (
            self._aging_after > 0
            and self._high_priority_streak >= self._aging_after
            and any(e.priority == EventPriority.BACKGROUND for e in self._heap)
        ):
            event = self._pop_background()
            self._high_priority_streak = 0
            if not self._heap:
                self._not_empty.clear()
            return event

        event = heapq.heappop(self._heap)
        if event.priority == EventPriority.BACKGROUND:
            self._high_priority_streak = 0
        else:
            self._high_priority_streak += 1
        if not self._heap:
            self._not_empty.clear()
        return event

    def _pop_background(self) -> DaemonEvent:
        """Remove and return the oldest BACKGROUND event (heap rebuild)."""
        background = [
            e for e in self._heap if e.priority == EventPriority.BACKGROUND
        ]
        chosen = min(background, key=lambda e: e.sequence)
        self._heap = [e for e in self._heap if e.event_id != chosen.event_id]
        heapq.heapify(self._heap)
        return chosen
