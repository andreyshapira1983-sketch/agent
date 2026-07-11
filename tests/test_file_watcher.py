"""Tests for the async daemon file watcher (plan item 2.2).

The async tests drive :class:`FileWatcher` with a fully controllable clock: a
``StepClock`` whose ``sleep`` advances a fake ``now`` and runs an optional
per-poll hook, so a test can mutate the filesystem *between* the watcher's polls
and simulate many seconds in milliseconds. A hard ``run_async`` timeout means a
broken loop can never hang the suite; no real long sleeps are used.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.file_watcher import (
    DEFAULT_DEBOUNCE,
    DEFAULT_POLL_INTERVAL,
    FileChange,
    FileWatcher,
)

TEST_TIMEOUT = 5.0
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def run_async(coro):
    """Run a coroutine with a bounded overall timeout."""
    return asyncio.run(asyncio.wait_for(coro, timeout=TEST_TIMEOUT))


class StepClock:
    """Fake clock: ``sleep`` advances ``now`` and fires a per-poll hook.

    ``sleep`` returns immediately (no real waiting), advances the clock by the
    requested delay, and — after advancing — invokes ``on_sleep(count)`` so a
    test can create/modify/delete files just before the watcher's next scan.
    """

    def __init__(self, start: datetime) -> None:
        self.moment = start
        self.count = 0
        self.on_sleep = None

    def now(self) -> datetime:
        return self.moment

    async def sleep(self, delay: float) -> None:
        await asyncio.sleep(0)
        self.moment = self.moment + timedelta(seconds=max(0.0, delay))
        self.count += 1
        if self.on_sleep is not None:
            result = self.on_sleep(self.count)
            if inspect.isawaitable(result):
                await result


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _touch_newer(path: Path, text: str, clock: StepClock) -> None:
    """Write ``text`` and force a strictly newer mtime.

    Filesystems can coalesce writes into the same mtime tick; the watcher's
    signature is ``(mtime_ns, size)`` so a same-size rewrite at the same tick
    would look unchanged. Bumping mtime to the fake clock keeps changes visible
    deterministically regardless of filesystem timestamp granularity.
    """
    _write(path, text)
    stamp = clock.moment.timestamp() + 1.0
    import os

    os.utime(path, (stamp, stamp))


# ── construction / validation ────────────────────────────────────────────


def test_rejects_empty_paths():
    with pytest.raises(ValueError):
        FileWatcher([])


def test_rejects_non_positive_poll_interval(workspace: Path):
    with pytest.raises(ValueError):
        FileWatcher([workspace], poll_interval=0)


def test_rejects_negative_debounce(workspace: Path):
    with pytest.raises(ValueError):
        FileWatcher([workspace], debounce=-1)


def test_defaults_exposed():
    assert DEFAULT_POLL_INTERVAL > 0
    assert DEFAULT_DEBOUNCE >= 0


# ── pure scan / diff logic ────────────────────────────────────────────────


def test_scan_lists_directory_files(workspace: Path):
    _write(workspace / "a.txt", "1")
    _write(workspace / "b.txt", "2")
    watcher = FileWatcher([workspace])
    snap = watcher.scan()
    assert (workspace / "a.txt") in snap
    assert (workspace / "b.txt") in snap


def test_scan_pattern_filter(workspace: Path):
    _write(workspace / "keep.jsonl", "1")
    _write(workspace / "skip.txt", "2")
    watcher = FileWatcher([workspace], patterns=["*.jsonl"])
    snap = watcher.scan()
    assert (workspace / "keep.jsonl") in snap
    assert (workspace / "skip.txt") not in snap


def test_scan_missing_target_is_empty(workspace: Path):
    watcher = FileWatcher([workspace / "does-not-exist"])
    assert watcher.scan() == {}


def test_diff_created_modified_deleted():
    p1, p2, p3 = Path("a"), Path("b"), Path("c")
    old = {p1: (1, 1), p2: (1, 1)}
    new = {p2: (2, 2), p3: (1, 1)}
    diff = FileWatcher._diff(old, new)
    assert diff == {p3: "created", p2: "modified", p1: "deleted"}


def test_merge_pending_created_then_deleted_cancels(workspace: Path):
    watcher = FileWatcher([workspace])
    watcher._merge_pending({Path("x"): "created"})
    watcher._merge_pending({Path("x"): "deleted"})
    assert watcher._pending == {}


def test_merge_pending_created_then_modified_stays_created(workspace: Path):
    watcher = FileWatcher([workspace])
    watcher._merge_pending({Path("x"): "created"})
    watcher._merge_pending({Path("x"): "modified"})
    assert watcher._pending == {Path("x"): "created"}


# ── async: detection ──────────────────────────────────────────────────────


def _run_watcher(watcher: FileWatcher, clock: StepClock, batches: list):
    """Wire ``on_change`` to record and stop; return the run coroutine."""

    def on_change(batch):
        batches.append(batch)
        watcher.stop()

    watcher._on_change = on_change
    return watcher.run()


def test_detects_new_file(workspace: Path):
    clock = StepClock(START)
    inbox = workspace / "inbox"
    inbox.mkdir()
    watcher = FileWatcher(
        [inbox], now=clock.now, sleep=clock.sleep, poll_interval=1.0, debounce=0.3
    )
    batches: list = []

    def on_sleep(count: int):
        if count == 1:
            _write(inbox / "item.jsonl", "new entry")

    clock.on_sleep = on_sleep
    run_async(_run_watcher(watcher, clock, batches))

    assert len(batches) == 1
    assert batches[0] == [FileChange(kind="created", path=inbox / "item.jsonl")]
    assert watcher.batches == 1
    assert watcher.changes == 1


def test_detects_modified_file(workspace: Path):
    clock = StepClock(START)
    cfg = workspace / "config.json"
    _touch_newer(cfg, "old", clock)
    watcher = FileWatcher(
        [cfg], now=clock.now, sleep=clock.sleep, poll_interval=1.0, debounce=0.3
    )
    batches: list = []

    def on_sleep(count: int):
        if count == 1:
            _touch_newer(cfg, "changed contents", clock)

    clock.on_sleep = on_sleep
    run_async(_run_watcher(watcher, clock, batches))

    assert batches == [[FileChange(kind="modified", path=cfg)]]


def test_detects_deleted_file(workspace: Path):
    clock = StepClock(START)
    victim = workspace / "gone.txt"
    _write(victim, "here")
    watcher = FileWatcher(
        [workspace], now=clock.now, sleep=clock.sleep, poll_interval=1.0, debounce=0.3
    )
    batches: list = []

    def on_sleep(count: int):
        if count == 1:
            victim.unlink()

    clock.on_sleep = on_sleep
    run_async(_run_watcher(watcher, clock, batches))

    assert batches == [[FileChange(kind="deleted", path=victim)]]


def test_coalesces_burst_into_single_batch(workspace: Path):
    """Several rapid writes across polls collapse into one settled batch."""
    clock = StepClock(START)
    cfg = workspace / "c.json"
    watcher = FileWatcher(
        [workspace], now=clock.now, sleep=clock.sleep, poll_interval=1.0, debounce=0.5
    )
    batches: list = []

    def on_sleep(count: int):
        if count == 1:
            _touch_newer(cfg, "v1", clock)
        elif count == 2:
            _touch_newer(cfg, "v2 longer", clock)
        elif count == 3:
            _touch_newer(cfg, "v3", clock)
        # count 4 quiet -> debounce elapses -> single batch

    clock.on_sleep = on_sleep
    run_async(_run_watcher(watcher, clock, batches))

    assert len(batches) == 1
    # created + subsequent modifies coalesce to a single created entry.
    assert batches[0] == [FileChange(kind="created", path=cfg)]
    assert watcher.batches == 1


def test_no_change_emits_nothing_and_no_self_echo(workspace: Path):
    """A stable watched set produces no batches — the watcher never writes."""
    clock = StepClock(START)
    _write(workspace / "stable.txt", "constant")
    watcher = FileWatcher(
        [workspace], now=clock.now, sleep=clock.sleep, poll_interval=1.0, debounce=0.3
    )
    batches: list = []
    watcher._on_change = lambda batch: batches.append(batch)

    def on_sleep(count: int):
        if count >= 5:
            watcher.stop()

    clock.on_sleep = on_sleep
    run_async(watcher.run())

    assert batches == []
    assert watcher.batches == 0


def test_survives_missing_directory_then_appears(workspace: Path):
    """A watched dir that does not exist yet is tolerated; later files fire."""
    clock = StepClock(START)
    inbox = workspace / "later" / "inbox"
    watcher = FileWatcher(
        [inbox], now=clock.now, sleep=clock.sleep, poll_interval=1.0, debounce=0.3
    )
    batches: list = []

    def on_change(batch):
        batches.append(batch)
        watcher.stop()

    watcher._on_change = on_change

    def on_sleep(count: int):
        if count == 2:  # first poll: dir absent (no error); now create it
            _write(inbox / "req.jsonl", "hello")

    clock.on_sleep = on_sleep
    run_async(watcher.run())

    assert batches == [[FileChange(kind="created", path=inbox / "req.jsonl")]]


def test_emit_existing_reports_baseline_as_created(workspace: Path):
    clock = StepClock(START)
    _write(workspace / "pre.txt", "already here")
    watcher = FileWatcher(
        [workspace],
        now=clock.now,
        sleep=clock.sleep,
        poll_interval=1.0,
        debounce=0.3,
        emit_existing=True,
    )
    batches: list = []
    run_async(_run_watcher(watcher, clock, batches))

    assert batches == [[FileChange(kind="created", path=workspace / "pre.txt")]]


# ── async: lifecycle / robustness ─────────────────────────────────────────


def test_callback_error_does_not_kill_watcher(workspace: Path):
    clock = StepClock(START)
    watcher = FileWatcher(
        [workspace], now=clock.now, sleep=clock.sleep, poll_interval=1.0, debounce=0.3
    )
    calls: list = []

    def boom(batch):
        calls.append(batch)
        watcher.stop()
        raise RuntimeError("callback bug")

    watcher._on_change = boom

    def on_sleep(count: int):
        if count == 1:
            _write(workspace / "f.txt", "x")

    clock.on_sleep = on_sleep
    # Should not raise: the callback error is logged, not propagated.
    run_async(watcher.run())
    assert len(calls) == 1
    assert watcher.running is False


def test_run_twice_is_rejected(workspace: Path):
    clock = StepClock(START)
    watcher = FileWatcher([workspace], now=clock.now, sleep=clock.sleep)

    async def scenario():
        watcher._running = True  # simulate an in-progress run
        with pytest.raises(RuntimeError):
            await watcher.run()

    run_async(scenario())


def test_cancellation_propagates(workspace: Path):
    """Cancelling the watcher task raises CancelledError and clears running."""

    async def scenario():
        watcher = FileWatcher([workspace], poll_interval=10.0)
        task = asyncio.ensure_future(watcher.run())
        await asyncio.sleep(0)  # let it start and enter the wait
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert watcher.running is False

    run_async(scenario())


def test_stop_before_run_exits_without_scanning(workspace: Path):
    _write(workspace / "should-not-fire.txt", "x")
    clock = StepClock(START)
    watcher = FileWatcher(
        [workspace], now=clock.now, sleep=clock.sleep, emit_existing=True
    )
    batches: list = []
    watcher._on_change = lambda batch: batches.append(batch)
    watcher.stop()
    run_async(watcher.run())
    # emit_existing would have reported the file, but stop short-circuits first.
    assert batches == []


def test_sleep_or_wake_returns_true_when_notified(workspace: Path):
    async def scenario():
        watcher = FileWatcher([workspace], poll_interval=10.0)
        watcher.notify()
        woke = await watcher._sleep_or_wake(10.0)
        assert woke is True

    run_async(scenario())


def test_stop_is_idempotent(workspace: Path):
    watcher = FileWatcher([workspace])
    watcher.stop()
    watcher.stop()
    assert watcher.stopped is True


def test_file_change_to_dict():
    change = FileChange(kind="created", path=Path("a") / "b.txt")
    assert change.to_dict() == {"kind": "created", "path": str(Path("a") / "b.txt")}


def test_recursive_scan_detects_nested_file(workspace: Path):
    clock = StepClock(START)
    root = workspace / "tree"
    root.mkdir()
    watcher = FileWatcher(
        [root],
        now=clock.now,
        sleep=clock.sleep,
        poll_interval=1.0,
        debounce=0.3,
        recursive=True,
    )
    batches: list = []
    nested = root / "sub" / "deep.txt"

    def on_sleep(count: int):
        if count == 1:
            _write(nested, "nested")

    clock.on_sleep = on_sleep
    run_async(_run_watcher(watcher, clock, batches))
    assert batches == [[FileChange(kind="created", path=nested)]]


def test_flush_pending_on_stop_before_debounce(workspace: Path):
    """A pending change is still emitted when stop() interrupts the debounce."""
    clock = StepClock(START)
    watcher = FileWatcher(
        [workspace], now=clock.now, sleep=clock.sleep, poll_interval=1.0, debounce=100.0
    )
    batches: list = []
    watcher._on_change = lambda batch: batches.append(batch)
    target = workspace / "pending.txt"

    def on_sleep(count: int):
        if count == 1:
            _write(target, "x")
        elif count == 2:
            watcher.stop()  # debounce (100s) has not elapsed yet

    clock.on_sleep = on_sleep
    run_async(watcher.run())
    assert batches == [[FileChange(kind="created", path=target)]]


def test_run_without_callback_does_not_raise(workspace: Path):
    clock = StepClock(START)
    watcher = FileWatcher(
        [workspace], now=clock.now, sleep=clock.sleep, poll_interval=1.0, debounce=0.3
    )

    def on_sleep(count: int):
        if count == 1:
            _write(workspace / "f.txt", "x")
        elif count >= 3:
            watcher.stop()

    clock.on_sleep = on_sleep
    run_async(watcher.run())  # on_change is None: batch is counted, not delivered
    assert watcher.batches == 1
