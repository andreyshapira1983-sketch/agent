"""Single-instance lock for the daemon; the OS lock, not the lock file, is the source of truth."""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from types import TracebackType
from typing import IO, Self

if os.name == "nt":  # pragma: no cover - platform-specific import
    import msvcrt
else:  # pragma: no cover - platform-specific import
    import fcntl

# Relative on purpose: no machine-specific absolute path in the repository.
DEFAULT_LOCK_PATH = Path("data") / "daemon.lock"

# Windows byte locks are mandatory (a locked byte is unreadable), so lock one byte far past
# the JSON diagnostics at offset 0; POSIX flock is whole-file and ignores the offset.
_WINDOWS_LOCK_OFFSET = 0x40000000  # 1 GiB — well past any diagnostics payload


class AlreadyRunningError(RuntimeError):
    """Raised when another daemon instance already holds the lock."""

    def __init__(self, path: Path, details: dict | None = None) -> None:
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
    """Cross-platform single-instance lock backed by an OS advisory file lock."""

    def __init__(self, path: Path | str = DEFAULT_LOCK_PATH) -> None:
        self._path = Path(path)
        self._fh: IO[str] | None = None

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

    def acquire(self) -> SingleInstanceLock:
        """Acquire the lock, or raise :class:`AlreadyRunningError` if busy."""
        if self._fh is not None:
            return self
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # No truncation: the holder's diagnostics stay readable if the lock is busy.
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
        self._write_details(fh)
        self._fh = fh
        return self

    def release(self) -> None:
        """Drop the OS lock and close the handle; idempotent.

        The file is never unlinked: unlinking is racy on POSIX and could allow two owners.
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

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
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
