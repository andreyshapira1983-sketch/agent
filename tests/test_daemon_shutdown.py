"""Tests for daemon graceful shutdown (plan item 1.2).

All scenarios use short bounded timeouts — no real long sleeps, no signals
sent to the test process, no background tasks left behind.
"""
from __future__ import annotations

import asyncio
import signal

import pytest

from app.daemon import DaemonLoop, DaemonLoopError

# Hard cap so a broken shutdown can never hang the test suite.
TEST_TIMEOUT = 5.0


def run_async(coro):
    """Run a coroutine with a bounded overall timeout."""
    return asyncio.run(asyncio.wait_for(coro, timeout=TEST_TIMEOUT))


async def noop_handler(reasons):  # pragma: no cover - trivial
    del reasons


# ── task tracking ──────────────────────────────────────────────────────────


def test_spawn_tracks_and_untracks_tasks():
    async def scenario():
        daemon = DaemonLoop(on_wake=noop_handler)
        run_task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)

        started = asyncio.Event()
        release = asyncio.Event()

        async def job():
            started.set()
            await release.wait()

        task = daemon.spawn(job(), name="job-1")
        await started.wait()
        assert daemon.active_tasks == 1
        assert task.get_name() == "job-1"

        release.set()
        await task
        assert daemon.active_tasks == 0

        await daemon.shutdown()
        await run_task

    run_async(scenario())


def test_spawn_refused_during_shutdown():
    async def scenario():
        daemon = DaemonLoop(on_wake=noop_handler)
        run_task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)

        await daemon.shutdown()
        with pytest.raises(DaemonLoopError):
            daemon.spawn(asyncio.sleep(0))
        await run_task

    run_async(scenario())


# ── draining and cancellation ──────────────────────────────────────────────


def test_shutdown_drains_in_flight_task():
    """A task that finishes within drain_timeout completes normally."""

    async def scenario():
        daemon = DaemonLoop(on_wake=noop_handler, drain_timeout=2.0)
        run_task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)

        result: list[str] = []

        async def job():
            await asyncio.sleep(0.01)
            result.append("done")

        daemon.spawn(job())
        await daemon.shutdown()
        await run_task
        return result

    assert run_async(scenario()) == ["done"]


def test_shutdown_cancels_task_exceeding_drain_timeout():
    async def scenario():
        daemon = DaemonLoop(on_wake=noop_handler, drain_timeout=0.05)
        run_task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)

        cancelled = asyncio.Event()

        async def stuck_job():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        daemon.spawn(stuck_job())
        await daemon.shutdown()
        await run_task
        return cancelled.is_set()

    assert run_async(scenario()) is True


def test_shutdown_survives_failing_task():
    """A task that raises must not break shutdown."""

    async def scenario():
        daemon = DaemonLoop(on_wake=noop_handler, drain_timeout=1.0)
        run_task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)

        async def bad_job():
            raise RuntimeError("boom")

        daemon.spawn(bad_job())
        await daemon.shutdown()
        await run_task

    run_async(scenario())


# ── idempotency ────────────────────────────────────────────────────────────


def test_repeated_shutdown_is_safe():
    async def scenario():
        daemon = DaemonLoop(on_wake=noop_handler)
        run_task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)

        await daemon.shutdown()
        await daemon.shutdown()  # second call must not raise
        await asyncio.gather(daemon.shutdown(), daemon.shutdown())
        await run_task
        assert daemon.shutting_down is True

    run_async(scenario())


def test_concurrent_shutdown_callers_all_return():
    async def scenario():
        daemon = DaemonLoop(on_wake=noop_handler, drain_timeout=1.0)
        run_task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)

        async def slow_job():
            await asyncio.sleep(0.02)

        daemon.spawn(slow_job())
        await asyncio.gather(*(daemon.shutdown() for _ in range(3)))
        await run_task

    run_async(scenario())


def test_shutdown_without_run_completes():
    """Shutdown on a never-started loop must not hang."""

    async def scenario():
        daemon = DaemonLoop(on_wake=noop_handler)
        await daemon.shutdown()
        with pytest.raises(DaemonLoopError):
            await daemon.run()

    run_async(scenario())


# ── resource closing ───────────────────────────────────────────────────────


def test_close_callbacks_run_once_in_order():
    async def scenario():
        daemon = DaemonLoop(on_wake=noop_handler)
        order: list[str] = []

        def close_sync():
            order.append("sync")

        async def close_async():
            order.append("async")

        daemon.add_close_callback(close_sync)
        daemon.add_close_callback(close_async)

        run_task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)
        await daemon.shutdown()
        await daemon.shutdown()
        await run_task
        return order

    assert run_async(scenario()) == ["sync", "async"]


def test_failing_close_callback_does_not_block_others():
    async def scenario():
        daemon = DaemonLoop(on_wake=noop_handler)
        order: list[str] = []

        def bad():
            raise OSError("cannot close")

        daemon.add_close_callback(bad)
        daemon.add_close_callback(lambda: order.append("second"))

        run_task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)
        await daemon.shutdown()
        await run_task
        return order

    assert run_async(scenario()) == ["second"]


# ── signal handling ────────────────────────────────────────────────────────


def test_signal_callback_triggers_graceful_stop():
    """Simulate signal delivery via the internal loop-thread callback."""

    async def scenario():
        daemon = DaemonLoop(on_wake=noop_handler)
        run_task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)

        daemon._on_signal(signal.SIGINT)  # noqa: SLF001 - deliberate white-box test
        await run_task
        assert daemon.shutting_down is True
        assert daemon.running is False

    run_async(scenario())


def test_signal_fallback_handler_triggers_stop():
    """The signal.signal fallback path (Windows) must also stop the loop."""

    async def scenario():
        daemon = DaemonLoop(on_wake=noop_handler)
        run_task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)

        daemon._signal_fallback(signal.SIGINT, None)  # noqa: SLF001
        await run_task
        assert daemon.shutting_down is True

    run_async(scenario())


def test_run_with_handle_signals_installs_and_restores():
    """run(handle_signals=True) must restore previous handlers on exit."""

    async def scenario():
        before = signal.getsignal(signal.SIGINT)
        daemon = DaemonLoop(on_wake=noop_handler)
        run_task = asyncio.create_task(daemon.run(handle_signals=True))
        await asyncio.sleep(0.01)

        assert daemon.running is True
        await daemon.shutdown()
        await run_task
        return before, signal.getsignal(signal.SIGINT)

    before, after = run_async(scenario())
    assert after == before


def test_shutdown_with_negative_drain_timeout_rejected():
    async def scenario():
        daemon = DaemonLoop(on_wake=noop_handler)
        with pytest.raises(ValueError):
            await daemon.shutdown(drain_timeout=-1)

    run_async(scenario())


def test_invalid_drain_timeout_in_constructor():
    with pytest.raises(ValueError):
        DaemonLoop(on_wake=noop_handler, drain_timeout=-0.1)


def test_wake_ignored_after_shutdown_started():
    """Wake-ups arriving during shutdown must not restart dispatching."""

    async def scenario():
        batches: list[list[str]] = []

        async def handler(reasons):
            batches.append(list(reasons))

        daemon = DaemonLoop(on_wake=handler)
        run_task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)

        await daemon.shutdown()
        daemon.wake("too-late")
        await run_task
        return batches

    assert run_async(scenario()) == []
