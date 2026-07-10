"""Tests for the persistent daemon event loop (plan item 1.1).

All tests drive the loop through asyncio with short bounded timeouts —
no real long sleeps, no background processes left behind.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from app.daemon import IDLE_TIMEOUT_REASON, DaemonLoop, DaemonLoopError

# Hard cap so a broken loop can never hang the test suite.
TEST_TIMEOUT = 5.0


def run_async(coro):
    """Run a coroutine with a bounded overall timeout."""
    return asyncio.run(asyncio.wait_for(coro, timeout=TEST_TIMEOUT))


class Recorder:
    """Wake handler that records every dispatched batch of reasons."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []
        self.dispatched = asyncio.Event()

    async def __call__(self, reasons: list[str]) -> None:
        self.batches.append(list(reasons))
        self.dispatched.set()


def test_loop_does_not_exit_after_one_pass():
    """The loop must keep waiting for events after handling one wake-up."""

    async def scenario():
        recorder = Recorder()
        daemon = DaemonLoop(on_wake=recorder)
        task = asyncio.create_task(daemon.run())

        daemon.wake("first")
        await recorder.dispatched.wait()
        recorder.dispatched.clear()

        assert daemon.running is True
        assert not task.done()

        daemon.wake("second")
        await recorder.dispatched.wait()

        daemon.request_stop()
        await task
        return recorder.batches

    batches = run_async(scenario())
    assert batches == [["first"], ["second"]]


def test_stop_before_any_wake():
    """A loop that is stopped immediately exits cleanly with no dispatches."""

    async def scenario():
        recorder = Recorder()
        daemon = DaemonLoop(on_wake=recorder)
        task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)  # let the loop start waiting
        daemon.request_stop()
        await task
        return recorder.batches, daemon

    batches, daemon = run_async(scenario())
    assert batches == []
    assert daemon.iterations == 0
    assert daemon.running is False
    assert daemon.stop_requested is True


def test_request_stop_is_idempotent():
    """Calling request_stop multiple times must not raise."""

    async def scenario():
        daemon = DaemonLoop(on_wake=Recorder())
        task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)
        daemon.request_stop()
        daemon.request_stop()
        await task
        daemon.request_stop()  # after the loop finished — still safe

    run_async(scenario())


def test_reasons_are_batched():
    """Reasons accumulated before the loop drains are delivered together."""

    async def scenario():
        recorder = Recorder()
        daemon = DaemonLoop(on_wake=recorder)
        # Wake BEFORE the loop starts: reasons must be queued, not lost.
        daemon.wake("a")
        daemon.wake("b")
        task = asyncio.create_task(daemon.run())
        await recorder.dispatched.wait()
        daemon.request_stop()
        await task
        return recorder.batches

    batches = run_async(scenario())
    assert batches == [["a", "b"]]


def test_wake_threadsafe_from_other_thread():
    """wake_threadsafe must wake the loop from a foreign thread."""

    async def scenario():
        recorder = Recorder()
        daemon = DaemonLoop(on_wake=recorder)
        task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)

        thread = threading.Thread(
            target=daemon.wake_threadsafe, args=("from-thread",)
        )
        thread.start()
        await recorder.dispatched.wait()
        thread.join(timeout=TEST_TIMEOUT)

        daemon.request_stop()
        await task
        return recorder.batches

    batches = run_async(scenario())
    assert batches == [["from-thread"]]


def test_wake_threadsafe_before_start_queues_reason():
    """wake_threadsafe before run() must queue the reason for the first pass."""

    async def scenario():
        recorder = Recorder()
        daemon = DaemonLoop(on_wake=recorder)
        daemon.wake_threadsafe("early")
        task = asyncio.create_task(daemon.run())
        await recorder.dispatched.wait()
        daemon.request_stop()
        await task
        return recorder.batches

    batches = run_async(scenario())
    assert batches == [["early"]]


def test_handler_error_does_not_kill_loop(caplog):
    """A handler exception is logged and the loop keeps serving wake-ups."""

    dispatched = asyncio.Event()
    calls: list[list[str]] = []

    async def flaky(reasons: list[str]) -> None:
        calls.append(list(reasons))
        dispatched.set()
        if reasons == ["boom"]:
            raise ValueError("boom")

    async def scenario():
        daemon = DaemonLoop(on_wake=flaky)
        task = asyncio.create_task(daemon.run())

        daemon.wake("boom")
        await dispatched.wait()
        dispatched.clear()

        assert not task.done()
        daemon.wake("ok")
        await dispatched.wait()

        daemon.request_stop()
        await task

    run_async(scenario())
    assert calls == [["boom"], ["ok"]]
    assert any("wake handler failed" in r.message for r in caplog.records)


def test_idle_timeout_wakes_loop():
    """With idle_timeout set, the loop wakes on its own with the marker reason."""

    async def scenario():
        recorder = Recorder()
        daemon = DaemonLoop(on_wake=recorder, idle_timeout=0.01)
        task = asyncio.create_task(daemon.run())
        await recorder.dispatched.wait()
        daemon.request_stop()
        await task
        return recorder.batches

    batches = run_async(scenario())
    assert batches[0] == [IDLE_TIMEOUT_REASON]


def test_invalid_idle_timeout_rejected():
    with pytest.raises(ValueError):
        DaemonLoop(on_wake=Recorder(), idle_timeout=0)
    with pytest.raises(ValueError):
        DaemonLoop(on_wake=Recorder(), idle_timeout=-1)


def test_cannot_run_twice_concurrently():
    """A second concurrent run() on the same instance must fail fast."""

    async def scenario():
        daemon = DaemonLoop(on_wake=Recorder())
        task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)
        with pytest.raises(DaemonLoopError):
            await daemon.run()
        daemon.request_stop()
        await task

    run_async(scenario())


def test_cannot_restart_finished_loop():
    """run() after the loop has finished must fail fast (no silent restart)."""

    async def scenario():
        daemon = DaemonLoop(on_wake=Recorder())
        task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)
        daemon.request_stop()
        await task
        with pytest.raises(DaemonLoopError):
            await daemon.run()

    run_async(scenario())


def test_cancellation_propagates():
    """Cancelling the run() task must actually cancel it (shutdown hook for 1.2)."""

    async def scenario():
        daemon = DaemonLoop(on_wake=Recorder())
        task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert daemon.running is False

    run_async(scenario())


def test_wake_after_stop_is_safe():
    """Waking a stopped loop must not raise (late producers are harmless)."""

    async def scenario():
        daemon = DaemonLoop(on_wake=Recorder())
        task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0)
        daemon.request_stop()
        await task
        daemon.wake("late")
        daemon.wake_threadsafe("late-threadsafe")

    run_async(scenario())
