"""Single-instance guarantee for the daemon (plan item 1.3).

Only one continuous-service daemon may run at a time. This module provides
:class:`SingleInstanceLock`, a small cross-platform lock file that:

- refuses to start a second daemon while one is already running;
- recovers automatically from a *stale* lock left behind by a crashed process
  (the operating system releases the lock when the owning process dies, so a
  new instance can acquire it without manual clean-up);
- never removes a lock that another *live* process still holds;
- releases the OS lock on normal shutdown but leaves the lock *file* on disk;
- reports a clear, actionable message identifying the running instance.

Source of truth
---------------
The **OS lock is the only source of truth** for whether a daemon is running.
The presence of ``data/daemon.lock`` on disk does **not** mean a daemon is
alive: the file is a permanent service artefact that persists after the daemon
exits. ``release()`` deliberately never unlinks it — deleting the pathname is
racy on POSIX and could permit two simultaneous owners (see
:meth:`SingleInstanceLock.release`). A leftover, unlocked file is therefore the
normal, healthy resting state; the next :meth:`SingleInstanceLock.acquire`
re-locks it and rewrites the diagnostics.

Mechanism
---------
The lock relies on an OS-level advisory exclusive lock on an open file
descriptor — ``fcntl.flock`` on POSIX and ``msvcrt.locking`` on Windows. This
is the existing project idiom (see :mod:`core.file_lock`) and has one very
convenient property: the OS drops the lock automatically when the holding
process exits *for any reason*, including a crash or ``kill -9``. That is what
makes stale locks self-healing without racy "is this PID still alive?" games.

The file also stores small JSON diagnostics (pid, hostname, start time) so an
operator — or the "already running" error message — can point at the culprit.
Those diagnostics are advisory only; correctness comes from the OS lock, not
from the PID written in the file.

This module does not import or replace ``agent_tick.py``; the single-shot
fallback mode never needs the daemon lock.
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from types import TracebackType
from typing import IO, Optional, Type

if os.name == "nt":  # pragma: no cover - platform-specific import
    import msvcrt
else:  # pragma: no cover - platform-specific import
    import fcntl

# Default location for the daemon lock. Relative on purpose: no machine-specific
# absolute paths are baked into the repository (matches the project convention
# used by, e.g., the approval inbox default under ``data/``).
DEFAULT_LOCK_PATH = Path("data") / "daemon.lock"

# Windows uses *mandatory* byte-range locking, so a locked byte cannot even be
# read by another handle. We therefore lock a single byte far beyond the tiny
# JSON payload region: the lock still guarantees exclusivity, but the
# diagnostics at offset 0 stay readable for the "already running" reporter and
# for operators. (POSIX ``flock`` is advisory and whole-file, so this offset is
# irrelevant there.)
_WINDOWS_LOCK_OFFSET = 0x40000000  # 1 GiB — well past any diagnostics payload


class AlreadyRunningError(RuntimeError):
    """Raised when another daemon instance already holds the lock.

    The :attr:`details` mapping carries whatever diagnostics could be read from
    the existing lock file (``pid``, ``hostname``, ``started_at``); it may be
    empty if the file was unreadable or being rewritten concurrently.
    """

    def __init__(self, path: Path, details: Optional[dict] = None) -> None:
        self.path = path
        self.details = details or {}
        pid = self.details.get("pid")
        host = self.details.get("hostname")
        who = ""
        if pid is not None:
            who = f" (held by pid {pid}"
            if host:
                who += f" on {host}"
            who += ")"
        super().__init__(
            f"another daemon instance is already running{who}; "
            f"lock file: {path}"
        )


class SingleInstanceLock:
    """Cross-platform single-instance lock backed by an OS advisory file lock.

    Use as a context manager::

        with SingleInstanceLock():
            run_the_daemon()

    or explicitly::

        lock = SingleInstanceLock(path)
        lock.acquire()      # raises AlreadyRunningError if busy
        try:
            ...
        finally:
            lock.release()   # idempotent

    Parameters
    ----------
    path:
        Lock file location. Defaults to :data:`DEFAULT_LOCK_PATH`. The parent
        directory is created on acquire.
    """

    def __init__(self, path: Path | str = DEFAULT_LOCK_PATH) -> None:
        self._path = Path(path)
        self._fh: Optional[IO[str]] = None

    # ── observability ────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        """The lock file path."""
        return self._path

    @property
    def held(self) -> bool:
        """True while this object owns the lock."""
        return self._fh is not None

    # ── acquire / release ────────────────────────────────────────────────

    def acquire(self) -> "SingleInstanceLock":
        """Acquire the lock, or raise :class:`AlreadyRunningError` if busy.

        Idempotent: acquiring an already-held lock is a no-op. A stale lock from
        a crashed process is acquired transparently because the OS has already
        released it.
        """
        if self._fh is not None:
            return self
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Open without truncating so an existing PID stays readable if we fail
        # to take the lock (r+ needs the file to exist; fall back to a+).
        try:
            fh = self._path.open("r+")
        except FileNotFoundError:
            fh = self._path.open("a+")
        try:
            self._lock_file(fh)
        except OSError:
            details = self._read_details(fh)
            fh.close()
            raise AlreadyRunningError(self._path, details) from None
        # We own the lock now: record diagnostics for operators.
        self._write_details(fh)
        self._fh = fh
        return self

    def release(self) -> None:
        """Release the lock but keep the lock file on disk. Idempotent.

        Releasing only drops the OS lock, closes the file handle, and clears
        internal state. The lock *file* is intentionally **not** unlinked.

        Deleting the pathname in ``release()`` is racy on POSIX: the OS lock is
        tied to the open file *description* (inode), not to the name. Between
        our unlock and our ``unlink`` a second process can open the same file
        and take the lock on that inode; our ``unlink`` then removes the name
        out from under it, and a third process is free to create a brand-new
        ``daemon.lock`` and acquire a *second, simultaneous* lock — two live
        owners at once. Leaving the file in place closes that window: the OS
        lock (not the file's presence) is the single source of truth, so a
        leftover, unlocked file is the normal, healthy resting state and the
        next :meth:`acquire` simply re-locks and rewrites its diagnostics.
        """
        fh = self._fh
        if fh is None:
            return
        self._fh = None
        try:
            self._unlock_file(fh)
        finally:
            fh.close()

    # ── context manager ──────────────────────────────────────────────────

    def __enter__(self) -> "SingleInstanceLock":
        return self.acquire()

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.release()

    # ── platform-specific locking ────────────────────────────────────────

    @staticmethod
    def _lock_file(fh: IO[str]) -> None:
        """Take a non-blocking exclusive lock; raise OSError if held elsewhere."""
        if os.name == "nt":  # pragma: no cover - exercised only on Windows
            fh.seek(_WINDOWS_LOCK_OFFSET)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_file(fh: IO[str]) -> None:
        if os.name == "nt":  # pragma: no cover - exercised only on Windows
            try:
                fh.seek(_WINDOWS_LOCK_OFFSET)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # ── diagnostics payload ──────────────────────────────────────────────

    def _write_details(self, fh: IO[str]) -> None:
        payload = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "started_at": time.time(),
        }
        fh.seek(0)
        fh.truncate()
        json.dump(payload, fh)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:  # pragma: no cover - fsync unsupported on some FS
            pass

    @staticmethod
    def _read_details(fh: IO[str]) -> dict:
        try:
            fh.seek(0)
            raw = fh.read()
        except OSError:
            return {}
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}
