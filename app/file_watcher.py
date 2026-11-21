"""Async stat-polling file watcher that wakes the daemon loop when watched paths change."""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

NowFn = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]
ChangeKind = Literal["created", "modified", "deleted"]
ChangeCallback = Callable[["list[FileChange]"], "Awaitable[None] | None"]

# Seconds between snapshot scans.
DEFAULT_POLL_INTERVAL = 1.0

# Seconds a change burst must stay quiet before its batch is emitted.
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

    Args:
        paths: files and/or directories; a directory contributes its child files,
            a missing target contributes nothing until it appears.
        on_change: sync or async callback for each settled batch; its errors are logged.
        patterns: fnmatch globs matched against file names.
        emit_existing: report files present at startup as ``created``.
    """

    def __init__(
        self,
        paths: Iterable[Path | str],
        *,
        on_change: ChangeCallback | None = None,
        patterns: Iterable[str] | None = None,
        now: NowFn = _now,
        sleep: SleepFn | None = None,
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
        self._pending_since: datetime | None = None
        self._wake_event = asyncio.Event()
        self._stopped = False
        self._running = False
        self._batches = 0
        self._changes = 0
        if emit_existing:
            # An empty baseline makes the first scan report existing files as created.
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

    def _signature(self, path: Path) -> tuple[int, int] | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def scan(self) -> dict[Path, tuple[int, int]]:
        """Return the (mtime_ns, size) snapshot of watched files; unreadable paths are omitted."""
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
        """Poll and emit debounced change batches until :meth:`stop` or cancellation."""
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
        """Sleep up to ``delay`` seconds or until notified/stopped; return True if woken early."""
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
        except Exception:
            logger.exception("file watcher on_change callback failed")
