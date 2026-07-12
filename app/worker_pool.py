"""Bounded asyncio worker pool for the daemon dispatcher (plan item 3.2).

Consumes :class:`~app.priority_event_queue.DaemonEvent` values from an existing
:class:`~app.priority_event_queue.PriorityEventQueue` (3.1) without owning the
queue store, the :class:`~app.daemon.DaemonLoop`, or per-task timeouts (3.3).

Contract
--------
* At most ``max_workers`` handler coroutines run concurrently (default ``2``).
* Workers are asyncio tasks — they must ``await`` and must not block the loop
  with long synchronous work.
* An exception in one handler is logged and isolated; other workers continue.
* :meth:`WorkerPool.shutdown` stops accepting new work (closes the queue),
  waits up to ``drain_timeout`` for in-flight handlers, then cancels stragglers.
* Repeated :meth:`shutdown` is safe.

``agent_tick.py`` is untouched.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from app.priority_event_queue import (
    DaemonEvent,
    PriorityEventQueue,
    PriorityEventQueueClosed,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_WORKERS = 2
DEFAULT_DRAIN_TIMEOUT = 10.0
_CANCEL_GRACE_SECONDS = 5.0

EventHandler = Callable[[DaemonEvent], Awaitable[None]]


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
    """

    def __init__(
        self,
        queue: PriorityEventQueue,
        handler: EventHandler,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        drain_timeout: float = DEFAULT_DRAIN_TIMEOUT,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if drain_timeout < 0:
            raise ValueError("drain_timeout must be >= 0")
        self._queue = queue
        self._handler = handler
        self._max_workers = max_workers
        self._drain_timeout = drain_timeout
        self._workers: list[asyncio.Task[None]] = []
        self._in_flight: set[asyncio.Task[None]] = set()
        self._started = False
        self._shutting_down = False
        self._run_task: asyncio.Task[None] | None = None
        self._processed = 0
        self._errors = 0
        self._cancelled = 0

    @property
    def max_workers(self) -> int:
        return self._max_workers

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
            done, still = await asyncio.wait(
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
            if self._shutting_down and self._queue.qsize() == 0:
                # Still allow draining events already queued before close.
                pass
            try:
                event = await self._queue.get()
            except PriorityEventQueueClosed:
                return
            except asyncio.CancelledError:
                raise

            handler_task = asyncio.create_task(
                self._run_handler(event),
                name=f"daemon-handler-{worker_id}-{event.event_id}",
            )
            self._in_flight.add(handler_task)
            try:
                await handler_task
            except asyncio.CancelledError:
                self._cancelled += 1
                raise
            finally:
                self._in_flight.discard(handler_task)

    async def _run_handler(self, event: DaemonEvent) -> None:
        try:
            await self._handler(event)
            self._processed += 1
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad event must not kill the pool
            self._errors += 1
            logger.exception(
                "worker pool handler failed for event %s kind=%s",
                event.event_id,
                event.kind,
            )
