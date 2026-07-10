"""Long-running daemon core: the main asyncio event loop.

This module is the foundation for the continuous-service mode of the agent
(plan item 1.1). Unlike ``agent_tick.py`` — which wakes up once, does one
bounded pass, and exits — :class:`DaemonLoop` stays alive, sleeps until it is
woken, and dispatches each wake-up to a handler.

Scope of this module (deliberately minimal)
-------------------------------------------
- An asyncio loop that does NOT exit after one pass.
- A safe wake-up mechanism (:meth:`DaemonLoop.wake` from inside the loop,
  :meth:`DaemonLoop.wake_threadsafe` from other threads).
- A cooperative stop (:meth:`DaemonLoop.request_stop`) so callers can end the
  loop deterministically. Full graceful-shutdown semantics (signals, task
  draining, timeouts) belong to plan item 1.2 and are NOT implemented here.
- An extension point for future event sources (timers, file watchers, task
  queues): they only need a reference to the loop object and call
  ``wake(reason)`` / ``wake_threadsafe(reason)``.

``agent_tick.py`` remains the supported single-shot fallback mode; nothing
here replaces or imports it.

Usage
-----
::

    async def handler(reasons: list[str]) -> None:
        ...  # react to wake-up reasons

    daemon = DaemonLoop(on_wake=handler)
    asyncio.run(daemon.run())          # runs until daemon.request_stop()

"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Reason recorded when the loop wakes because idle_timeout elapsed rather
# than because someone called wake().
IDLE_TIMEOUT_REASON = "idle-timeout"

WakeHandler = Callable[[list[str]], Awaitable[None]]


class DaemonLoopError(RuntimeError):
    """Raised on invalid use of :class:`DaemonLoop` (e.g. running twice)."""


class DaemonLoop:
    """Minimal persistent asyncio event loop with explicit wake-ups.

    The loop sleeps on an internal :class:`asyncio.Event`. Producers wake it
    with a *reason* string; all reasons accumulated while the loop was busy
    are delivered together (batched) to ``on_wake`` on the next iteration.

    Parameters
    ----------
    on_wake:
        Async callback invoked with the list of wake reasons collected since
        the previous iteration. Exceptions raised by the handler are logged
        and do not terminate the loop.
    idle_timeout:
        Optional number of seconds after which the loop wakes on its own with
        the reason :data:`IDLE_TIMEOUT_REASON`. ``None`` (default) means the
        loop sleeps until explicitly woken. This is the hook future timer
        sources (plan item 2.1) can build on.
    """

    def __init__(
        self,
        on_wake: WakeHandler,
        *,
        idle_timeout: Optional[float] = None,
    ) -> None:
        if idle_timeout is not None and idle_timeout <= 0:
            raise ValueError("idle_timeout must be positive or None")
        self._on_wake = on_wake
        self._idle_timeout = idle_timeout
        self._wake_event = asyncio.Event()
        self._pending_reasons: list[str] = []
        self._stop_requested = False
        self._running = False
        self._finished = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._iterations = 0

    # ── observability ────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        """True while :meth:`run` is executing."""
        return self._running

    @property
    def stop_requested(self) -> bool:
        """True once :meth:`request_stop` has been called."""
        return self._stop_requested

    @property
    def iterations(self) -> int:
        """Number of completed wake/dispatch cycles."""
        return self._iterations

    # ── producer API ─────────────────────────────────────────────────────

    def wake(self, reason: str = "manual") -> None:
        """Wake the loop from within the same event loop / thread."""
        self._pending_reasons.append(reason)
        self._wake_event.set()

    def wake_threadsafe(self, reason: str = "manual") -> None:
        """Wake the loop from another thread.

        Safe to call before the loop starts (the reason is queued) and after
        it stops (the call is a no-op apart from recording the reason).
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            # Loop not running yet (or already gone): queue the reason so it
            # is picked up on the first iteration.
            self._pending_reasons.append(reason)
            return
        loop.call_soon_threadsafe(self.wake, reason)

    def request_stop(self) -> None:
        """Ask the loop to exit after the current iteration.

        Idempotent: calling it multiple times is safe.
        """
        self._stop_requested = True
        self._wake_event.set()

    # ── core loop ────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Run until :meth:`request_stop` is called.

        The loop:
        1. waits for a wake-up (or ``idle_timeout``),
        2. drains all pending reasons,
        3. dispatches them to ``on_wake`` (errors are logged, not raised),
        4. repeats.

        Raises
        ------
        DaemonLoopError
            If the loop is already running or has already finished. Each
            :class:`DaemonLoop` instance runs at most once.
        """
        if self._running:
            raise DaemonLoopError("DaemonLoop is already running")
        if self._finished:
            raise DaemonLoopError("DaemonLoop cannot be restarted; create a new instance")

        self._running = True
        self._loop = asyncio.get_running_loop()
        logger.info("daemon loop started (idle_timeout=%s)", self._idle_timeout)
        try:
            while not self._stop_requested:
                reasons = await self._wait_for_wake()
                if self._stop_requested:
                    break
                await self._dispatch(reasons)
                self._iterations += 1
        finally:
            self._running = False
            self._finished = True
            logger.info("daemon loop stopped after %d iteration(s)", self._iterations)

    async def _wait_for_wake(self) -> list[str]:
        """Sleep until woken or until ``idle_timeout`` elapses."""
        if not self._pending_reasons and not self._stop_requested:
            if self._idle_timeout is None:
                await self._wake_event.wait()
            else:
                try:
                    await asyncio.wait_for(
                        self._wake_event.wait(), timeout=self._idle_timeout
                    )
                except asyncio.TimeoutError:
                    self._pending_reasons.append(IDLE_TIMEOUT_REASON)
        self._wake_event.clear()
        reasons = self._pending_reasons
        self._pending_reasons = []
        return reasons

    async def _dispatch(self, reasons: list[str]) -> None:
        """Invoke the handler; a failing handler must not kill the loop."""
        if not reasons:
            return
        try:
            await self._on_wake(reasons)
        except asyncio.CancelledError:
            # Cancellation must propagate: it is how shutdown (1.2) will
            # eventually interrupt in-flight work.
            raise
        except Exception:  # noqa: BLE001 — logged, loop must survive handler bugs
            logger.exception("daemon wake handler failed for reasons=%s", reasons)
