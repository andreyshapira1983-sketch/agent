"""Async file watcher for daemon event sources (plan item 2.2).

This is the second event source for the continuous-service mode (after the
in-loop timer scheduler, 2.1). It watches a set of files and/or directories —
typically the approval inbox and applicable config files — and wakes the daemon
loop when they change, so a human dropping an approval or editing config does not
have to wait for the next scheduler tick.

Design choices
--------------
- **No heavy dependency.** Rather than pulling in ``watchdog`` (a native,
  platform-specific dependency that would need justification and lock-file
  churn), this uses a lightweight *stat snapshot* diff on a cooperative poll.
  Each poll is a cheap ``stat`` of a small, known set of paths and yields the
  event loop between polls, so it never blocks the loop and never busy-spins.
- **Debounced / coalesced.** A burst of rapid writes (e.g. an editor saving a
  config repeatedly, or several inbox appends) is collapsed into a single
  batch: the watcher only emits once the watched set has been quiet for a
  bounded ``debounce`` window.
- **Survives a missing directory.** A watched path that does not exist yet is
  simply treated as empty; when it appears the entries are reported as created.
  A path that vanishes mid-scan is treated as absent, never as an error.
- **No self-echo.** The watcher only ever *reads* (``stat``); it never writes to
  the paths it watches, so it cannot trigger itself. Unchanged files produce no
  event because their signature is identical across polls.
- **Injectable time.** ``now`` and ``sleep`` are injectable so tests drive many
  simulated seconds in milliseconds with a fake clock instead of real sleeps.

Time is fully injectable and the watcher cooperates with the daemon loop
(1.1/1.2) rather than owning it: a caller ``spawn``s :meth:`run` as a task and
``stop`` / cancels it during graceful shutdown. ``agent_tick.py`` is untouched
and does not import this module.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Literal, Optional

logger = logging.getLogger(__name__)

NowFn = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]
ChangeKind = Literal["created", "modified", "deleted"]
ChangeCallback = Callable[["list[FileChange]"], "Optional[Awaitable[None]]"]

# Default cadence between snapshot scans. Small enough to feel responsive,
# large enough that the periodic ``stat`` of a handful of paths is negligible.
DEFAULT_POLL_INTERVAL = 1.0

# Default quiet window a change burst must settle for before a batch is emitted.
DEFAULT_DEBOUNCE = 0.3


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FileChange:
    """A single detected change to a watched path."""

    kind: ChangeKind
    path: Path

    def to_dict(self) -> dict:
        return {"kind": self.kind, "path": str(self.path)}


# path -> (mtime_ns, size); a path absent from the map is "not present".
_Snapshot = "dict[Path, tuple[int, int]]"


class FileWatcher:
    """Watch files/directories and emit coalesced change batches.

    Parameters
    ----------
    paths:
        Files and/or directories to watch. Directory targets contribute their
        direct child files (optionally recursively). Non-existent targets are
        allowed and simply contribute nothing until they appear.
    on_change:
        Optional callback (sync or async) invoked with a non-empty list of
        :class:`FileChange` whenever a settled batch is detected. This is the
        hook the daemon loop uses to ``wake`` its dispatcher. Callback errors
        are logged and never kill the watcher.
    patterns:
        Optional iterable of ``fnmatch`` glob patterns (matched against each
        file's name). When given, only matching files are watched.
    now / sleep:
        Injectable clock and awaitable sleep (default UTC ``datetime.now`` and
        :func:`asyncio.sleep`), so tests use a controllable clock.
    poll_interval:
        Seconds between snapshot scans. Must be > 0.
    debounce:
        Quiet window (seconds, >= 0) a change burst must settle for before the
        batch is emitted, coalescing rapid repeated writes.
    recursive:
        Whether directory targets are scanned recursively. Defaults to False.
    emit_existing:
        Whether files already present at startup are reported as ``created`` on
        the first settled batch. Defaults to False (baseline is silent).
    """

    def __init__(
        self,
        paths: Iterable[Path | str],
        *,
        on_change: Optional[ChangeCallback] = None,
        patterns: Optional[Iterable[str]] = None,
        now: NowFn = _now,
        sleep: Optional[SleepFn] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        debounce: float = DEFAULT_DEBOUNCE,
        recursive: bool = False,
        emit_existing: bool = False,
    ) -> None:
        resolved = [Path(p) for p in paths]
        if not resolved:
            raise ValueError("at least one path must be watched")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if debounce < 0:
            raise ValueError("debounce must be >= 0")
        self._paths = resolved
        self._on_change = on_change
        self._patterns = tuple(patterns) if patterns is not None else None
        self._now = now
        self._sleep: SleepFn = sleep or asyncio.sleep
        self._poll_interval = float(poll_interval)
        self._debounce = float(debounce)
        self._recursive = recursive
        self._snapshot: dict[Path, tuple[int, int]] = {}
        self._pending: dict[Path, ChangeKind] = {}
        self._pending_since: Optional[datetime] = None
        self._wake_event = asyncio.Event()
        self._stopped = False
        self._running = False
        self._batches = 0
        self._changes = 0
        if emit_existing:
            # Leave the baseline empty so the first scan reports existing files
            # as created.
            self._snapshot = {}
            self._baseline_taken = True
        else:
            self._baseline_taken = False

    # ── observability ────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        """True while :meth:`run` is executing."""
        return self._running

    @property
    def stopped(self) -> bool:
        """True once :meth:`stop` has been requested."""
        return self._stopped

    @property
    def batches(self) -> int:
        """Number of settled change batches emitted."""
        return self._batches

    @property
    def changes(self) -> int:
        """Total number of individual changes emitted across all batches."""
        return self._changes

    # ── control ──────────────────────────────────────────────────────────

    def notify(self) -> None:
        """Wake the watcher so it re-scans immediately. Idempotent."""
        self._wake_event.set()

    def stop(self) -> None:
        """Ask the watcher to exit after the current wait. Idempotent."""
        self._stopped = True
        self._wake_event.set()

    # ── scanning ─────────────────────────────────────────────────────────

    def _match(self, path: Path) -> bool:
        if self._patterns is None:
            return True
        from fnmatch import fnmatch

        return any(fnmatch(path.name, pattern) for pattern in self._patterns)

    def _signature(self, path: Path) -> Optional[tuple[int, int]]:
        try:
            st = path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def scan(self) -> dict[Path, tuple[int, int]]:
        """Return the current signature snapshot of all watched files.

        A path that raises while being read (e.g. it vanished between listing
        and ``stat``) is simply omitted, so a transient race never surfaces as
        an error — it shows up as a delete/create on a later poll instead.
        """
        out: dict[Path, tuple[int, int]] = {}
        for target in self._paths:
            try:
                if target.is_dir():
                    walker = target.rglob("*") if self._recursive else target.iterdir()
                    for child in walker:
                        if not child.is_file() or not self._match(child):
                            continue
                        sig = self._signature(child)
                        if sig is not None:
                            out[child] = sig
                elif target.is_file() and self._match(target):
                    sig = self._signature(target)
                    if sig is not None:
                        out[target] = sig
            except OSError:
                # Target disappeared mid-scan; treat as absent this round.
                continue
        return out

    @staticmethod
    def _diff(
        old: dict[Path, tuple[int, int]],
        new: dict[Path, tuple[int, int]],
    ) -> dict[Path, ChangeKind]:
        changes: dict[Path, ChangeKind] = {}
        for path, sig in new.items():
            if path not in old:
                changes[path] = "created"
            elif old[path] != sig:
                changes[path] = "modified"
        for path in old:
            if path not in new:
                changes[path] = "deleted"
        return changes

    def _merge_pending(self, diff: dict[Path, ChangeKind]) -> None:
        for path, kind in diff.items():
            previous = self._pending.get(path)
            if previous is None:
                self._pending[path] = kind
            elif previous == "created" and kind == "deleted":
                # Created then deleted within the same quiet window: net no-op.
                del self._pending[path]
            elif previous == "created" and kind == "modified":
                # Still a net creation.
                self._pending[path] = "created"
            else:
                self._pending[path] = kind

    # ── main loop ────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Run until :meth:`stop` is called or the task is cancelled.

        Each poll takes a fresh snapshot and diffs it against the last. New
        changes reset a quiet timer; a batch is only emitted once no further
        change has appeared for ``debounce`` seconds, coalescing bursts.
        Cancellation propagates so the daemon's graceful shutdown can cancel it.
        """
        if self._running:
            raise RuntimeError("FileWatcher is already running")
        self._running = True
        if not self._baseline_taken:
            self._snapshot = self.scan()
            self._baseline_taken = True
        logger.info(
            "file watcher started (paths=%d, poll=%.2fs, debounce=%.2fs)",
            len(self._paths),
            self._poll_interval,
            self._debounce,
        )
        try:
            while not self._stopped:
                await self._sleep_or_wake(self._poll_interval)
                if self._stopped:
                    break
                current = self.scan()
                diff = self._diff(self._snapshot, current)
                self._snapshot = current
                if diff:
                    self._merge_pending(diff)
                    self._pending_since = self._now()
                    continue
                if self._pending and self._debounce_elapsed():
                    await self._flush()
            # Emit whatever settled work remains before exiting cleanly.
            if self._pending:
                await self._flush()
        except asyncio.CancelledError:
            logger.info("file watcher cancelled")
            raise
        finally:
            self._running = False
            logger.info("file watcher stopped after %d batch(es)", self._batches)

    def _debounce_elapsed(self) -> bool:
        if self._pending_since is None:
            return True
        elapsed = (self._now() - self._pending_since).total_seconds()
        return elapsed >= self._debounce

    async def _flush(self) -> None:
        batch = [
            FileChange(kind=kind, path=path)
            for path, kind in sorted(self._pending.items(), key=lambda kv: str(kv[0]))
        ]
        self._pending = {}
        self._pending_since = None
        if not batch:
            return
        self._batches += 1
        self._changes += len(batch)
        logger.info(
            "file watcher batch %d: %d change(s)", self._batches, len(batch)
        )
        await self._emit(batch)

    async def _sleep_or_wake(self, delay: float) -> bool:
        """Sleep up to ``delay`` seconds or until notified/stopped.

        Returns True if woken early (by :meth:`notify` / :meth:`stop`) rather
        than by the sleep elapsing. Pending child futures are always cancelled
        and awaited so nothing is left dangling, including on cancellation.
        """
        if self._stopped or self._wake_event.is_set():
            self._wake_event.clear()
            return True
        sleeper = asyncio.ensure_future(self._sleep(delay))
        waiter = asyncio.ensure_future(self._wake_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {sleeper, waiter}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for future in (sleeper, waiter):
                if not future.done():
                    future.cancel()
            await asyncio.gather(sleeper, waiter, return_exceptions=True)
        woke = waiter in done
        if woke:
            self._wake_event.clear()
        return woke

    async def _emit(self, batch: list[FileChange]) -> None:
        if self._on_change is None:
            return
        try:
            result = self._on_change(batch)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — callback bugs must not kill the watcher
            logger.exception("file watcher on_change callback failed")
