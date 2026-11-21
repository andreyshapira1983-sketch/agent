"""Persistent asyncio daemon loop: explicit wake-ups, a handler call per wake, graceful stop."""
from __future__ import annotations

import asyncio
import inspect
import logging
import signal
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# Wake reason recorded when idle_timeout elapses without a wake() call.
IDLE_TIMEOUT_REASON = "idle-timeout"

# Seconds cancelled tasks get to unwind before shutdown abandons them; never blocks forever.
CANCEL_GRACE_SECONDS = 5.0

WakeHandler = Callable[[list[str]], Awaitable[None]]
CloseCallback = Callable[[], Any]


class DaemonLoopError(RuntimeError):
    """Raised on invalid use of :class:`DaemonLoop` (e.g. running twice)."""


class DaemonLoop:
    """Persistent asyncio event loop with explicit wake-ups.

    Args: on_wake gets the reasons collected since the last iteration (its errors are logged);
    idle_timeout wakes the loop on its own (None: sleep until woken); drain_timeout is how
    long shutdown waits for spawned tasks before cancelling them.
    """

    def __init__(
        self,
        on_wake: WakeHandler,
        *,
        idle_timeout: float | None = None,
        drain_timeout: float = 10.0,
    ) -> None:
        if idle_timeout is not None and idle_timeout <= 0:
            raise ValueError("idle_timeout must be positive or None")
        if drain_timeout < 0:
            raise ValueError("drain_timeout must be >= 0")
        self._on_wake = on_wake
        self._idle_timeout = idle_timeout
        self._drain_timeout = drain_timeout
        self._wake_event = asyncio.Event()
        self._pending_reasons: list[str] = []
        self._stop_requested = False
        self._running = False
        self._finished = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._iterations = 0
        self._tasks: set[asyncio.Task] = set()
        self._close_callbacks: list[CloseCallback] = []
        self._shutdown_started = False
        self._shutdown_complete = asyncio.Event()

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

    @property
    def shutting_down(self) -> bool:
        """True once shutdown has begun; new tasks are refused from then on."""
        return self._shutdown_started

    @property
    def active_tasks(self) -> int:
        """Number of tracked in-flight tasks."""
        return sum(1 for t in self._tasks if not t.done())

    def spawn(
        self, coro: Coroutine[Any, Any, Any], *, name: str | None = None
    ) -> asyncio.Task:
        """Start and track a task so shutdown can drain or cancel it."""
        if self._shutdown_started or self._stop_requested or self._finished:
            coro.close()
            raise DaemonLoopError("daemon is shutting down; not accepting new tasks")
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def add_close_callback(self, callback: CloseCallback) -> None:
        """Register a sync or async close callback; all run once, in order, at shutdown end."""
        self._close_callbacks.append(callback)

    def wake(self, reason: str = "manual") -> None:
        """Wake the loop from within the same event loop / thread."""
        self._pending_reasons.append(reason)
        self._wake_event.set()

    def wake_threadsafe(self, reason: str = "manual") -> None:
        """Wake the loop from another thread; if it is not running, just queue the reason."""
        loop = self._loop
        if loop is None or loop.is_closed():
            self._pending_reasons.append(reason)
            return
        loop.call_soon_threadsafe(self.wake, reason)

    def request_stop(self) -> None:
        """Ask the loop to exit after the current iteration (idempotent)."""
        self._stop_requested = True
        self._wake_event.set()

    async def run(self, *, handle_signals: bool = False) -> None:
        """Run until request_stop(); graceful shutdown runs exactly once on any exit.

        handle_signals installs SIGINT (and SIGTERM where supported) handlers that request a
        graceful stop. Raises DaemonLoopError if run twice: an instance runs at most once.
        """
        if self._running:
            raise DaemonLoopError("DaemonLoop is already running")
        if self._finished:
            raise DaemonLoopError("DaemonLoop cannot be restarted; create a new instance")

        self._running = True
        self._loop = asyncio.get_running_loop()
        installed = self._install_signal_handlers() if handle_signals else []
        logger.info("daemon loop started (idle_timeout=%s)", self._idle_timeout)
        try:
            while not self._stop_requested:
                reasons = await self._wait_for_wake()
                if self._stop_requested:
                    break
                await self._dispatch(reasons)
                self._iterations += 1
        finally:
            self._remove_signal_handlers(installed)
            await self._finalize_shutdown()
            self._running = False
            self._finished = True
            logger.info("daemon loop stopped after %d iteration(s)", self._iterations)

    async def shutdown(self, *, drain_timeout: float | None = None) -> None:
        """Request a graceful stop and wait until shutdown has completed.

        drain_timeout overrides the instance value; only the call that performs the drain uses it.
        """
        if drain_timeout is not None:
            if drain_timeout < 0:
                raise ValueError("drain_timeout must be >= 0")
            self._drain_timeout = drain_timeout
        self._shutdown_started = True
        self.request_stop()
        if not self._running and not self._shutdown_complete.is_set():
            # Loop not running: finalize here, or the wait below would never end.
            await self._finalize_shutdown()
            self._finished = True
        await self._shutdown_complete.wait()

    async def _finalize_shutdown(self) -> None:
        """Drain in-flight tasks, cancel stragglers, close resources. Idempotent."""
        if self._shutdown_complete.is_set():
            return
        self._shutdown_started = True
        try:
            await self._drain_tasks()
            await self._run_close_callbacks()
        finally:
            self._shutdown_complete.set()

    async def _drain_tasks(self) -> None:
        pending = {t for t in self._tasks if not t.done()}
        if not pending:
            return
        logger.info(
            "shutdown: draining %d in-flight task(s) for up to %.1fs",
            len(pending),
            self._drain_timeout,
        )
        done, still_pending = await asyncio.wait(pending, timeout=self._drain_timeout)
        self._log_task_results(done)
        if not still_pending:
            return
        logger.warning(
            "shutdown: cancelling %d task(s) that exceeded drain timeout",
            len(still_pending),
        )
        for task in still_pending:
            task.cancel()
        done, stuck = await asyncio.wait(still_pending, timeout=CANCEL_GRACE_SECONDS)
        self._log_task_results(done)
        for task in stuck:
            logger.error("shutdown: task %r ignored cancellation; abandoning it", task)

    @staticmethod
    def _log_task_results(done: set[asyncio.Task]) -> None:
        for task in done:
            if task.cancelled():
                logger.info("shutdown: task %r was cancelled", task.get_name())
                continue
            exc = task.exception()
            if exc is not None:
                logger.error(
                    "shutdown: task %r failed", task.get_name(), exc_info=exc
                )

    async def _run_close_callbacks(self) -> None:
        for callback in self._close_callbacks:
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("shutdown: close callback %r failed", callback)

    def _install_signal_handlers(self) -> list[tuple[signal.Signals, str, Any]]:
        """Install SIGINT/SIGTERM handlers; returns entries for later removal."""
        installed: list[tuple[signal.Signals, str, Any]] = []
        wanted = [signal.SIGINT]
        sigterm = getattr(signal, "SIGTERM", None)
        if sigterm is not None:
            wanted.append(sigterm)
        assert self._loop is not None  # noqa: S101 — type narrowing, guarded above
        for sig in wanted:
            previous = signal.getsignal(sig)
            try:
                self._loop.add_signal_handler(sig, self._on_signal, sig)
                installed.append((sig, "loop", previous))
            except (NotImplementedError, RuntimeError):
                try:
                    previous = signal.signal(sig, self._signal_fallback)
                    installed.append((sig, "signal", previous))
                except (ValueError, OSError) as exc:
                    # e.g. not the main thread, or signal unsupported here.
                    logger.warning("cannot install handler for %s: %s", sig, exc)
        return installed

    def _remove_signal_handlers(
        self, installed: list[tuple[signal.Signals, str, Any]]
    ) -> None:
        for sig, kind, previous in installed:
            try:
                if kind == "loop":
                    assert self._loop is not None  # noqa: S101 — type narrowing, guarded above
                    self._loop.remove_signal_handler(sig)
                signal.signal(sig, previous)
            except (ValueError, OSError, RuntimeError) as exc:
                logger.warning("cannot remove handler for %s: %s", sig, exc)

    def _on_signal(self, sig: signal.Signals) -> None:
        """Loop-thread signal callback: begin a graceful stop."""
        logger.info("received signal %s; starting graceful shutdown", sig.name)
        self._shutdown_started = True
        self.request_stop()

    def _signal_fallback(self, signum: int, frame: Any) -> None:
        """``signal.signal`` fallback handler (Windows / non-loop platforms)."""
        del frame
        sig = signal.Signals(signum)
        logger.info("received signal %s; starting graceful shutdown", sig.name)
        self._shutdown_started = True
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self.request_stop)
        else:
            self.request_stop()

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
                except TimeoutError:
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
            # Cancellation must propagate: shutdown uses it to interrupt in-flight work.
            raise
        except Exception:
            logger.exception("daemon wake handler failed for reasons=%s", reasons)
