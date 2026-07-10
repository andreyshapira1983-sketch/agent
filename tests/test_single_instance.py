"""Tests for the daemon single-instance guarantee (plan item 1.3).

The lock relies on an OS advisory lock tied to an open file description, so two
distinct :class:`SingleInstanceLock` objects conflict even inside one test
process — no second process or real sleeping is required.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.single_instance import (
    DEFAULT_LOCK_PATH,
    AlreadyRunningError,
    SingleInstanceLock,
)


@pytest.fixture
def lock_path(tmp_path: Path) -> Path:
    return tmp_path / "sub" / "daemon.lock"


# ── acquire / release basics ────────────────────────────────────────────────


def test_acquire_creates_file_and_marks_held(lock_path: Path):
    lock = SingleInstanceLock(lock_path)
    assert lock.held is False
    lock.acquire()
    try:
        assert lock.held is True
        assert lock_path.exists()
    finally:
        lock.release()


def test_release_removes_file_and_clears_held(lock_path: Path):
    lock = SingleInstanceLock(lock_path)
    lock.acquire()
    lock.release()
    assert lock.held is False
    assert not lock_path.exists()


def test_default_path_is_relative():
    # No machine-specific absolute path baked into the repo.
    assert not DEFAULT_LOCK_PATH.is_absolute()


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


def test_refusal_reports_holder_pid_and_hostname(lock_path: Path):
    first = SingleInstanceLock(lock_path)
    first.acquire()
    try:
        second = SingleInstanceLock(lock_path)
        with pytest.raises(AlreadyRunningError) as excinfo:
            second.acquire()
        err = excinfo.value
        assert err.details.get("pid") == os.getpid()
        assert "hostname" in err.details
        # Message is actionable: names the pid and the lock file.
        assert str(os.getpid()) in str(err)
        assert str(lock_path) in str(err)
    finally:
        first.release()


def test_second_instance_succeeds_after_first_releases(lock_path: Path):
    first = SingleInstanceLock(lock_path)
    first.acquire()
    first.release()

    second = SingleInstanceLock(lock_path)
    second.acquire()
    try:
        assert second.held is True
    finally:
        second.release()


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


def test_release_without_acquire_is_noop(lock_path: Path):
    lock = SingleInstanceLock(lock_path)
    lock.release()  # never acquired
    assert lock.held is False
    assert not lock_path.exists()


# ── stale lock recovery (simulated crash) ───────────────────────────────────


def test_stale_lock_file_is_recovered(lock_path: Path):
    # A crashed process leaves the file behind but the OS has dropped its lock.
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": 999999, "hostname": "ghost"}))

    lock = SingleInstanceLock(lock_path)
    lock.acquire()  # must succeed: no live holder
    try:
        assert lock.held is True
        # Diagnostics were rewritten to point at us.
        data = json.loads(lock_path.read_text())
        assert data["pid"] == os.getpid()
    finally:
        lock.release()


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
    assert not lock_path.exists()


def test_context_manager_refuses_nested_instance(lock_path: Path):
    with SingleInstanceLock(lock_path):
        with pytest.raises(AlreadyRunningError):
            with SingleInstanceLock(lock_path):
                pass  # pragma: no cover - never entered


# ── restart / re-run ────────────────────────────────────────────────────────


def test_lock_can_be_reacquired_across_restarts(lock_path: Path):
    for _ in range(3):
        lock = SingleInstanceLock(lock_path)
        lock.acquire()
        assert lock.held is True
        lock.release()
        assert not lock_path.exists()


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
