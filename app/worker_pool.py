"""Bounded asyncio worker pool for the daemon dispatcher (plan items 3.2–4.1).

Consumes :class:`~app.priority_event_queue.DaemonEvent` values from an existing
:class:`~app.priority_event_queue.PriorityEventQueue` (3.1) without owning the
queue store or the :class:`~app.daemon.DaemonLoop`.

Contract
--------
* At most ``max_workers`` handler coroutines run concurrently (default ``2``).
* Workers are asyncio tasks — they must ``await`` and must not block the loop
  with long synchronous work.
* An exception in one handler is logged and isolated; other workers continue.
* Each handler runs under a configurable ``task_timeout`` (3.3). On timeout the
  in-flight awaitable is cancelled, the event is **not** counted as success,
  and a structured log line records the timeout reason.
* Explicit cancellation (:class:`asyncio.CancelledError`) is also **not**
  counted as success.
* With an :class:`InFlightCheckpointStore`, each event becomes durable before
  handler code starts and is removed on every non-crash terminal path (4.1).
* :meth:`WorkerPool.shutdown` stops accepting new work (closes the queue),
  waits up to ``drain_timeout`` for in-flight handlers, then cancels stragglers.
* Repeated :meth:`shutdown` is safe.

``agent_tick.py`` is untouched.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from app.priority_event_queue import (
    DaemonEvent,
    PriorityEventQueue,
    PriorityEventQueueClosed,
)
from core.file_lock import exclusive_file_lock
from core.state_integrity import read_state_jsonl_unlocked, rewrite_state_jsonl_unlocked

logger = logging.getLogger(__name__)

DEFAULT_MAX_WORKERS = 2
DEFAULT_DRAIN_TIMEOUT = 10.0
# Default per-event handler budget (plan 3.3). ``None`` disables the limit.
DEFAULT_TASK_TIMEOUT = 60.0
_CANCEL_GRACE_SECONDS = 5.0

EventHandler = Callable[[DaemonEvent], Awaitable[None]]
Now = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_safe_event(event: DaemonEvent) -> dict:
    """Return a detached JSON-safe event snapshot for future recovery."""
    return json.loads(
        json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
    )


class InFlightCheckpointStore:
    """Atomic JSONL snapshot of events whose handlers are currently running.

    Each update is a locked read-modify-write so concurrent workers and
    processes cannot replace one another's checkpoints.  A process crash
    leaves the last atomic file intact for roadmap item 4.2 to inspect.
    """

    FORMAT = "daemon-in-flight-v1"

    def __init__(self, path: Path | str, *, now: Now = _now) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._now = now

    def checkpoint(self, event: DaemonEvent, *, worker_id: int) -> dict:
        """Durably upsert ``event`` before its handler starts."""
        timestamp = self._now().astimezone(timezone.utc).isoformat()
        record = {
            "format": self.FORMAT,
            "event_id": event.event_id,
            "worker_id": worker_id,
            "checkpointed_at": timestamp,
            "event": _json_safe_event(event),
        }
        with exclusive_file_lock(self._lock_path):
            records = self._load_unlocked()
            retained = [row for row in records if row.get("event_id") != event.event_id]
            retained.append(record)
            rewrite_state_jsonl_unlocked(self.path, retained)
        return record

    def remove(self, event_id: str) -> bool:
        """Atomically remove one checkpoint; repeated cleanup is harmless."""
        with exclusive_file_lock(self._lock_path):
            records = self._load_unlocked()
            retained = [row for row in records if row.get("event_id") != event_id]
            if len(retained) == len(records):
                return False
            rewrite_state_jsonl_unlocked(self.path, retained)
            return True

    def load(self) -> list[dict]:
        """Load valid checkpoints without performing startup recovery."""
        with exclusive_file_lock(self._lock_path):
            return self._load_unlocked()

    def _load_unlocked(self) -> list[dict]:
        return read_state_jsonl_unlocked(self.path)

    @property
    def _lock_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".lock")


class WorkerPoolError(RuntimeError):
    """Raised on invalid use of :class:`WorkerPool`."""


class WorkerPool:
    """Run up to ``max_workers`` concurrent handlers over a priority queue.

    Parameters
    ----------
    queue:
        Existing :class:`PriorityEventQueue` to consume (not duplicated).
    handler:
        Async callback invoked once per event. Exceptions are logged and do
        not stop the pool.
    max_workers:
        Concurrent handler limit (default :data:`DEFAULT_MAX_WORKERS`).
    drain_timeout:
        Seconds to wait for in-flight handlers during :meth:`shutdown`
        before cancelling them.
    task_timeout:
        Seconds allowed for a single handler. ``None`` disables per-task
        timeout. Timed-out work is cancelled and recorded in
        :attr:`timeout_count` (never as a successful :attr:`processed_count`).
    checkpoint_store:
        Optional durable in-flight store. When provided, an event checkpoint
        is atomically persisted before its handler begins and removed after
        success, error, timeout, or cancellation. Existing callers may omit it.
    """

    def __init__(
        self,
        queue: PriorityEventQueue,
        handler: EventHandler,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        drain_timeout: float = DEFAULT_DRAIN_TIMEOUT,
        task_timeout: Optional[float] = DEFAULT_TASK_TIMEOUT,
        checkpoint_store: InFlightCheckpointStore | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if drain_timeout < 0:
            raise ValueError("drain_timeout must be >= 0")
        if task_timeout is not None and task_timeout <= 0:
            raise ValueError("task_timeout must be positive or None")
        self._queue = queue
        self._handler = handler
        self._max_workers = max_workers
        self._drain_timeout = drain_timeout
        self._task_timeout = task_timeout
        self._checkpoint_store = checkpoint_store
        self._workers: list[asyncio.Task[None]] = []
        self._in_flight: set[asyncio.Task[None]] = set()
        self._started = False
        self._shutting_down = False
        self._run_task: asyncio.Task[None] | None = None
        self._processed = 0
        self._errors = 0
        self._cancelled = 0
        self._timeouts = 0

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @property
    def task_timeout(self) -> Optional[float]:
        return self._task_timeout

    @property
    def active_workers(self) -> int:
        return sum(1 for t in self._workers if not t.done())

    @property
    def in_flight(self) -> int:
        return len(self._in_flight)

    @property
    def shutting_down(self) -> bool:
        return self._shutting_down

    @property
    def processed_count(self) -> int:
        return self._processed

    @property
    def error_count(self) -> int:
        return self._errors

    @property
    def cancelled_count(self) -> int:
        return self._cancelled

    @property
    def timeout_count(self) -> int:
        return self._timeouts

    def metrics(self) -> dict[str, int | float | None]:
        """Snapshot counters for daemon-status / observability hooks."""
        return {
            "processed": self._processed,
            "errors": self._errors,
            "cancelled": self._cancelled,
            "timeouts": self._timeouts,
            "in_flight": len(self._in_flight),
            "active_workers": self.active_workers,
            "max_workers": self._max_workers,
            "task_timeout": self._task_timeout,
            "queue_size": self._queue.qsize(),
        }

    def start(self) -> None:
        """Spawn worker tasks on the running event loop.

        Idempotent while already started and not shutting down. Raises
        :class:`WorkerPoolError` if called during/after shutdown without a
        fresh pool instance.
        """
        if self._shutting_down:
            raise WorkerPoolError("cannot start WorkerPool during/after shutdown")
        if self._started and self._workers:
            return
        loop = asyncio.get_running_loop()
        self._started = True
        self._workers = [
            loop.create_task(self._worker_loop(i), name=f"daemon-worker-{i}")
            for i in range(self._max_workers)
        ]

    async def run(self) -> None:
        """Start workers (if needed) and wait until they all exit.

        Workers exit when the queue is closed and drained (see
        :meth:`shutdown`). Cancellation of this coroutine triggers shutdown.
        """
        self.start()
        try:
            await asyncio.gather(*self._workers, return_exceptions=True)
        except asyncio.CancelledError:
            await self.shutdown()
            raise

    async def shutdown(self, *, drain_timeout: Optional[float] = None) -> None:
        """Stop accepting work, drain or cancel in-flight handlers.

        Closes the shared :class:`PriorityEventQueue` so :meth:`get` waiters
        unblock. Safe to call repeatedly.
        """
        if self._shutting_down and not self._workers and not self._in_flight:
            return
        self._shutting_down = True
        timeout = self._drain_timeout if drain_timeout is None else drain_timeout
        if timeout < 0:
            raise ValueError("drain_timeout must be >= 0")

        if not self._queue.closed:
            self._queue.close()

        # Let worker loops notice the closed/empty queue and exit.
        if self._workers:
            try:
                await asyncio.wait(
                    self._workers,
                    timeout=timeout,
                    return_when=asyncio.ALL_COMPLETED,
                )
            except Exception:  # noqa: BLE001 - shutdown must proceed
                logger.exception("worker pool wait for workers failed")

        # Drain remaining in-flight handler tasks, then cancel stragglers.
        pending_handlers = [t for t in self._in_flight if not t.done()]
        if pending_handlers:
            _done, still = await asyncio.wait(
                pending_handlers,
                timeout=timeout,
                return_when=asyncio.ALL_COMPLETED,
            )
            for task in still:
                task.cancel()
                self._cancelled += 1
            if still:
                await asyncio.wait(
                    still,
                    timeout=_CANCEL_GRACE_SECONDS,
                    return_when=asyncio.ALL_COMPLETED,
                )

        for task in self._workers:
            if not task.done():
                task.cancel()
                self._cancelled += 1
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        self._in_flight.clear()

    async def _worker_loop(self, worker_id: int) -> None:
        while True:
            try:
                event = await self._queue.get()
            except PriorityEventQueueClosed:
                return
            except asyncio.CancelledError:
                raise

            handler_task = asyncio.create_task(
                self._run_handler(event, worker_id=worker_id),
                name=f"daemon-handler-{worker_id}-{event.event_id}",
            )
            self._in_flight.add(handler_task)
            try:
                await handler_task
            except asyncio.CancelledError:
                # Outer cancel (shutdown / run cancel) — not a successful event.
                if not handler_task.done():
                    handler_task.cancel()
                    try:
                        await handler_task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                self._cancelled += 1
                raise
            finally:
                self._in_flight.discard(handler_task)

    async def _run_handler(self, event: DaemonEvent, *, worker_id: int) -> None:
        checkpointed = False
        if self._checkpoint_store is not None:
            try:
                self._checkpoint_store.checkpoint(event, worker_id=worker_id)
                checkpointed = True
            except Exception:  # noqa: BLE001 - never run work without durability
                self._errors += 1
                logger.exception(
                    "worker pool checkpoint failed for event %s kind=%s; "
                    "handler not started",
                    event.event_id,
                    event.kind,
                )
                return
        try:
            if self._task_timeout is None:
                await self._handler(event)
            else:
                await asyncio.wait_for(
                    self._handler(event),
                    timeout=self._task_timeout,
                )
            self._processed += 1
        except asyncio.TimeoutError:
            self._timeouts += 1
            logger.warning(
                "worker pool task timed out event_id=%s kind=%s "
                "timeout_s=%s reason=task_timeout",
                event.event_id,
                event.kind,
                self._task_timeout,
            )
        except asyncio.CancelledError:
            # Propagate; caller records cancelled_count. Never count as success.
            raise
        except Exception:  # noqa: BLE001 - one bad event must not kill the pool
            self._errors += 1
            logger.exception(
                "worker pool handler failed for event %s kind=%s",
                event.event_id,
                event.kind,
            )
        finally:
            if checkpointed and self._checkpoint_store is not None:
                try:
                    self._checkpoint_store.remove(event.event_id)
                except Exception:  # noqa: BLE001 - preserve crash evidence
                    logger.exception(
                        "worker pool checkpoint cleanup failed for event %s kind=%s",
                        event.event_id,
                        event.kind,
                    )
