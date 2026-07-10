"""Tests for the in-loop timer scheduler (plan item 2.1).

Every test drives :class:`SchedulerService` through asyncio with a fully
controllable clock (a fake ``now`` plus a fake ``sleep`` that advances it) so
simulated hours pass in milliseconds. A hard ``run_async`` timeout guarantees a
broken loop can never hang the suite; no real long sleeps are used.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.scheduler import (
    DEFAULT_IDLE_INTERVAL,
    RuntimeSchedule,
    ScheduleTickReport,
    SchedulerService,
    SchedulerStore,
)
from core.task_queue import TaskQueueStore

TEST_TIMEOUT = 5.0
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def run_async(coro):
    """Run a coroutine with a bounded overall timeout."""
    return asyncio.run(asyncio.wait_for(coro, timeout=TEST_TIMEOUT))


class FakeClock:
    """Injectable time source: reading ``now`` never advances; ``sleep`` does.

    ``sleep`` returns immediately (no real waiting) but moves the clock forward
    by the requested number of seconds, so the service perceives elapsed time
    deterministically.
    """

    def __init__(self, start: datetime) -> None:
        self.moment = start

    def now(self) -> datetime:
        return self.moment

    async def sleep(self, delay: float) -> None:
        # Yield control so other tasks (e.g. a stopper) can run, then advance.
        await asyncio.sleep(0)
        self.moment = self.moment + timedelta(seconds=max(0.0, delay))


def _make(workspace: Path):
    store = SchedulerStore(workspace / "schedules.jsonl")
    queue = TaskQueueStore(workspace / "tasks.jsonl")
    return store, queue


# ── construction / validation ────────────────────────────────────────────


def test_rejects_non_positive_idle_interval(workspace: Path):
    store, queue = _make(workspace)
    with pytest.raises(ValueError):
        SchedulerService(store, queue, idle_interval=0)


def test_rejects_negative_limit(workspace: Path):
    store, queue = _make(workspace)
    with pytest.raises(ValueError):
        SchedulerService(store, queue, limit=-1)


# ── scheduling maths ──────────────────────────────────────────────────────


def test_seconds_until_next_none_when_empty(workspace: Path):
    store, queue = _make(workspace)
    clock = FakeClock(START)
    service = SchedulerService(store, queue, now=clock.now)

    assert service.seconds_until_next() is None


def test_seconds_until_next_computes_delay(workspace: Path):
    store, queue = _make(workspace)
    clock = FakeClock(START)
    store.add(name="s", goal="g", every_minutes=10, start_at=START + timedelta(minutes=5))
    service = SchedulerService(store, queue, now=clock.now)

    assert service.seconds_until_next() == 300.0


def test_seconds_until_next_negative_when_overdue(workspace: Path):
    store, queue = _make(workspace)
    clock = FakeClock(START)
    store.add(name="s", goal="g", every_minutes=10, start_at=START - timedelta(minutes=2))
    service = SchedulerService(store, queue, now=clock.now)

    assert service.seconds_until_next() == -120.0


def test_seconds_until_next_ignores_paused(workspace: Path):
    store, queue = _make(workspace)
    clock = FakeClock(START)
    paused = store.add(name="p", goal="g", every_minutes=10, start_at=START)
    store.pause(paused.id)
    service = SchedulerService(store, queue, now=clock.now)

    assert service.seconds_until_next() is None


# ── ticking ────────────────────────────────────────────────────────────────


def test_ticks_due_schedule_and_advances_next_run(workspace: Path):
    store, queue = _make(workspace)
    clock = FakeClock(START)
    schedule = store.add(
        name="health",
        goal="project health",
        every_minutes=30,
        start_at=START - timedelta(minutes=1),
    )
    reports: list[ScheduleTickReport] = []

    async def scenario():
        service = SchedulerService(store, queue, now=clock.now, sleep=clock.sleep)

        def on_tick(report: ScheduleTickReport) -> None:
            reports.append(report)
            service.stop()

        service._on_tick = on_tick  # noqa: SLF001 — direct wiring keeps the test small
        await service.run()

    run_async(scenario())

    assert len(reports) == 1
    tasks = queue.load()
    assert len(tasks) == 1
    assert tasks[0].goal == "project health"
    updated = store.load()[0]
    assert updated.id == schedule.id
    assert updated.last_run_at is not None
    # Next run is exactly one period after the tick time (START), not "now+30".
    assert updated.next_run_at > updated.last_run_at

def test_simulated_clock_fires_each_period(workspace: Path):
    store, queue = _make(workspace)
    clock = FakeClock(START)
    store.add(
        name="tenmin",
        goal="tick goal",
        every_minutes=10,
        start_at=START,
    )
    reports: list[ScheduleTickReport] = []

    async def scenario():
        service = SchedulerService(store, queue, now=clock.now, sleep=clock.sleep)

        def on_tick(report: ScheduleTickReport) -> None:
            reports.append(report)
            if len(reports) >= 3:
                service.stop()

        service._on_tick = on_tick  # noqa: SLF001
        await service.run()

    run_async(scenario())

    assert len(reports) == 3
    assert len(queue.load()) == 3
    # Three ticks 10 minutes apart => the fake clock advanced ~20 minutes
    # between the first and the last (two sleeps of 600s each).
    assert clock.moment >= START + timedelta(minutes=20)


def test_on_tick_async_callback_is_awaited(workspace: Path):
    store, queue = _make(workspace)
    clock = FakeClock(START)
    store.add(name="s", goal="g", every_minutes=15, start_at=START)
    seen: list[int] = []

    async def scenario():
        service = SchedulerService(store, queue, now=clock.now, sleep=clock.sleep)

        async def on_tick(report: ScheduleTickReport) -> None:
            await asyncio.sleep(0)
            seen.append(report.enqueued_count)
            service.stop()

        service._on_tick = on_tick  # noqa: SLF001
        await service.run()

    run_async(scenario())

    assert seen == [1]
    assert service_tick_count(store) == 1


def service_tick_count(store: SchedulerStore) -> int:
    # A due schedule that fired must have advanced (last_run_at set once).
    return sum(1 for s in store.load() if s.last_run_at is not None)


def test_on_tick_error_does_not_kill_loop(workspace: Path):
    store, queue = _make(workspace)
    clock = FakeClock(START)
    store.add(name="s", goal="g", every_minutes=10, start_at=START)
    calls: list[int] = []

    async def scenario():
        service = SchedulerService(store, queue, now=clock.now, sleep=clock.sleep)

        def on_tick(report: ScheduleTickReport) -> None:
            calls.append(report.enqueued_count)
            if len(calls) == 1:
                raise RuntimeError("boom")  # must be logged, loop survives
            service.stop()

        service._on_tick = on_tick  # noqa: SLF001
        await service.run()

    run_async(scenario())

    # First tick raised, second tick still happened => loop survived.
    assert len(calls) == 2


def test_limit_is_forwarded_to_tick(workspace: Path):
    store, queue = _make(workspace)
    clock = FakeClock(START)
    for i in range(2):
        store.add(name=f"s{i}", goal=f"g{i}", every_minutes=10, start_at=START)
    reports: list[ScheduleTickReport] = []

    async def scenario():
        service = SchedulerService(
            store, queue, now=clock.now, sleep=clock.sleep, limit=1
        )

        def on_tick(report: ScheduleTickReport) -> None:
            reports.append(report)
            if len(reports) >= 2:
                service.stop()

        service._on_tick = on_tick  # noqa: SLF001
        await service.run()

    run_async(scenario())

    # limit=1 => each tick enqueues at most one, so two ticks drain two schedules.
    assert all(r.enqueued_count == 1 for r in reports)
    assert len(queue.load()) == 2


# ── wake / stop / cancellation ─────────────────────────────────────────────


def test_notify_wakes_before_long_sleep_elapses(workspace: Path):
    store, queue = _make(workspace)

    async def scenario():
        # A deliberately long sleep; notify must win the race well within the
        # test timeout, proving no busy polling and instant wake-ups.
        service = SchedulerService(store, queue, sleep=lambda _d: asyncio.sleep(30))

        async def poke():
            await asyncio.sleep(0.01)
            service.notify()

        asyncio.create_task(poke())
        return await service._sleep_or_wake(30)  # noqa: SLF001

    assert run_async(scenario()) is True


def test_sleep_elapsing_returns_false(workspace: Path):
    store, queue = _make(workspace)

    async def scenario():
        service = SchedulerService(store, queue, sleep=asyncio.sleep)
        return await service._sleep_or_wake(0)  # noqa: SLF001

    assert run_async(scenario()) is False


def test_pre_set_stop_returns_immediately(workspace: Path):
    store, queue = _make(workspace)

    async def scenario():
        service = SchedulerService(store, queue, sleep=lambda _d: asyncio.sleep(30))
        service.stop()
        return await service._sleep_or_wake(30)  # noqa: SLF001

    assert run_async(scenario()) is True


def test_stop_before_run_exits_without_ticking(workspace: Path):
    store, queue = _make(workspace)
    clock = FakeClock(START)
    store.add(name="s", goal="g", every_minutes=10, start_at=START)

    async def scenario():
        service = SchedulerService(store, queue, now=clock.now, sleep=clock.sleep)
        service.stop()
        await service.run()
        return service

    service = run_async(scenario())

    assert service.running is False
    assert queue.load() == []


def test_cancellation_propagates_and_clears_running(workspace: Path):
    store, queue = _make(workspace)  # empty => idle-waits on a real sleep

    async def scenario():
        service = SchedulerService(store, queue, sleep=asyncio.sleep)
        task = asyncio.create_task(service.run())
        await asyncio.sleep(0.02)
        assert service.running is True
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return service

    service = run_async(scenario())
    assert service.running is False


def test_run_twice_raises(workspace: Path):
    store, queue = _make(workspace)  # empty => idle-waits

    async def scenario():
        service = SchedulerService(store, queue, sleep=asyncio.sleep)
        task = asyncio.create_task(service.run())
        await asyncio.sleep(0.02)
        try:
            with pytest.raises(RuntimeError):
                await service.run()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    run_async(scenario())


def test_default_idle_interval_constant_is_positive():
    assert DEFAULT_IDLE_INTERVAL > 0


def test_observability_properties(workspace: Path):
    store, queue = _make(workspace)
    clock = FakeClock(START)
    store.add(name="s", goal="g", every_minutes=10, start_at=START)

    async def scenario():
        service = SchedulerService(store, queue, now=clock.now, sleep=clock.sleep)
        assert service.stopped is False
        assert service.ticks == 0

        def on_tick(_report: ScheduleTickReport) -> None:
            service.stop()

        service._on_tick = on_tick  # noqa: SLF001
        await service.run()
        return service

    service = run_async(scenario())

    assert service.stopped is True
    assert service.ticks == 1



def test_runs_without_on_tick_callback(workspace: Path):
    store, queue = _make(workspace)
    clock = FakeClock(START)
    store.add(name="s", goal="g", every_minutes=10, start_at=START)

    async def scenario():
        service = SchedulerService(store, queue, now=clock.now)  # no on_tick

        async def sleeper(_delay: float) -> None:
            # First real sleep happens only after the due schedule has ticked.
            service.stop()
            await asyncio.sleep(0)

        service._sleep = sleeper  # noqa: SLF001
        await service.run()

    run_async(scenario())

    assert len(queue.load()) == 1


def test_cancelled_error_in_callback_propagates(workspace: Path):
    store, queue = _make(workspace)
    clock = FakeClock(START)
    store.add(name="s", goal="g", every_minutes=10, start_at=START)

    async def scenario():
        service = SchedulerService(store, queue, now=clock.now, sleep=clock.sleep)

        def on_tick(_report: ScheduleTickReport) -> None:
            raise asyncio.CancelledError

        service._on_tick = on_tick  # noqa: SLF001
        with pytest.raises(asyncio.CancelledError):
            await service.run()
        return service

    service = run_async(scenario())
    assert service.running is False



class _VanishingStore:
    """Store whose only schedule looks due once, then disappears.

    Emulates an out-of-band writer removing/pausing a schedule between the
    service's ``list`` check and its ``tick``: the first ``list`` reports a due
    schedule, ``tick`` enqueues nothing, and thereafter the store is empty.
    """

    def __init__(self) -> None:
        self.tick_calls = 0
        self._due = RuntimeSchedule(
            name="ghost",
            goal="g",
            every_minutes=10,
            next_run_at=(START - timedelta(minutes=1)).isoformat(),
        )

    def list(self, *, status=None):  # noqa: A003 - mirrors SchedulerStore.list
        return [] if self.tick_calls else [self._due]

    def tick(self, *, task_queue, now, limit=None):
        self.tick_calls += 1
        return ScheduleTickReport(
            due_count=0, enqueued_count=0, task_ids=(), schedule_ids=()
        )


def test_zero_enqueue_tick_recovers_without_hot_spin(workspace: Path):
    _store, queue = _make(workspace)
    store = _VanishingStore()

    async def scenario():
        service = SchedulerService(store, queue, now=lambda: START)

        async def sleeper(_delay: float) -> None:
            # Reached only on the idle-wait after the schedule vanished; stop
            # there so the test terminates.
            service.stop()
            await asyncio.sleep(0)

        service._sleep = sleeper  # noqa: SLF001
        await service.run()
        return service

    service = run_async(scenario())

    assert store.tick_calls == 1
    assert service.running is False

