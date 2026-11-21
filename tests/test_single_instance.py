"""Tests for the daemon single-instance guarantee (plan item 1.3).

The lock relies on an OS advisory lock tied to an open file description, so two
distinct :class:`SingleInstanceLock` objects conflict even inside one test
process — no second process or real sleeping is required.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.single_instance import (
    AlreadyRunningError,
    SingleInstanceLock,
)


@pytest.fixture
def lock_path(tmp_path: Path) -> Path:
    return tmp_path / "sub" / "daemon.lock"


# ── mutual exclusion ────────────────────────────────────────────────────────


def test_second_instance_is_refused_while_first_holds(lock_path: Path):
    first = SingleInstanceLock(lock_path)
    first.acquire()
    try:
        second = SingleInstanceLock(lock_path)
        with pytest.raises(AlreadyRunningError):
            second.acquire()
        assert second.held is False
    finally:
        first.release()


# ── idempotency ─────────────────────────────────────────────────────────────


def test_acquire_is_idempotent(lock_path: Path):
    lock = SingleInstanceLock(lock_path)
    lock.acquire()
    try:
        lock.acquire()  # second call must not raise or reopen
        assert lock.held is True
    finally:
        lock.release()


def test_release_is_idempotent(lock_path: Path):
    lock = SingleInstanceLock(lock_path)
    lock.acquire()
    lock.release()
    lock.release()  # no error, no effect
    assert lock.held is False


# ── stale lock recovery (simulated crash) ───────────────────────────────────


def test_corrupt_lock_file_does_not_crash_refusal(lock_path: Path):
    first = SingleInstanceLock(lock_path)
    first.acquire()
    try:
        # Corrupt the diagnostics while the lock is genuinely held.
        lock_path.write_text("not-json{{{")
        second = SingleInstanceLock(lock_path)
        with pytest.raises(AlreadyRunningError) as excinfo:
            second.acquire()
        # Unreadable details degrade gracefully to an empty mapping.
        assert excinfo.value.details == {}
    finally:
        first.release()


# ── context manager ─────────────────────────────────────────────────────────


def test_context_manager_acquires_and_releases(lock_path: Path):
    with SingleInstanceLock(lock_path) as lock:
        assert lock.held is True
        assert lock_path.exists()
    assert lock.held is False
    # File persists after the context exits; the OS lock is what was released.
    assert lock_path.exists()


def test_context_manager_refuses_nested_instance(lock_path: Path):
    with (
        SingleInstanceLock(lock_path),
        pytest.raises(AlreadyRunningError),
        SingleInstanceLock(lock_path),
    ):
        pass  # pragma: no cover - never entered


# ── restart / re-run ────────────────────────────────────────────────────────


def test_release_of_unheld_instance_does_not_delete_live_lock(lock_path: Path):
    holder = SingleInstanceLock(lock_path)
    holder.acquire()
    try:
        # A different object that never acquired must not remove the live file.
        bystander = SingleInstanceLock(lock_path)
        bystander.release()
        assert lock_path.exists()
        assert holder.held is True
    finally:
        holder.release()
