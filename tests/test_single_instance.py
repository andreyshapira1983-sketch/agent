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


def test_release_keeps_file_and_clears_held(lock_path: Path):
    # release() drops the OS lock but intentionally leaves the file on disk:
    # the file is a permanent artefact, the OS lock is the source of truth.
    lock = SingleInstanceLock(lock_path)
    lock.acquire()
    lock.release()
    assert lock.held is False
    # The leftover file is normal and must not block a fresh acquire.
    second = SingleInstanceLock(lock_path)
    second.acquire()
    try:
        assert second.held is True
    finally:
        second.release()


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
    # File persists after the context exits; the OS lock is what was released.
    assert lock_path.exists()


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
        # File remains between "restarts"; each acquire re-locks the same file.
        assert lock_path.exists()


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


# -- diagnostics are rewritten on each fresh acquire -------------------------


def test_reacquire_rewrites_diagnostics(lock_path: Path):
    first = SingleInstanceLock(lock_path)
    first.acquire()
    first.release()

    # Simulate a leftover file carrying stale, foreign diagnostics.
    lock_path.write_text(
        json.dumps({"pid": 123, "hostname": "stale-host", "started_at": 1.0})
    )

    second = SingleInstanceLock(lock_path)
    second.acquire()
    try:
        data = json.loads(lock_path.read_text())
        assert data["pid"] == os.getpid()
        assert data["hostname"] and data["hostname"] != "stale-host"
        assert data["started_at"] > 1.0
    finally:
        second.release()


# -- real cross-process mutual exclusion ------------------------------------

import multiprocessing as mp  # noqa: E402 - grouped with its worker helpers

_PROC_TIMEOUT = 30.0


def _drain_one(result_q):
    # Bounded get: raises queue.Empty on timeout so the test fails fast instead
    # of hanging if a child never reports.
    return result_q.get(timeout=_PROC_TIMEOUT)


def _hold_lock_worker(path_str, acquired_evt, release_evt, result_q):
    """Child: acquire, announce ownership, hold until told, then release."""
    from app.single_instance import AlreadyRunningError, SingleInstanceLock

    lock = SingleInstanceLock(path_str)
    try:
        lock.acquire()
    except AlreadyRunningError:
        result_q.put("hold:refused")
        return
    result_q.put("hold:acquired")
    acquired_evt.set()
    # Bounded wait so a broken test can never pin this process forever.
    release_evt.wait(timeout=_PROC_TIMEOUT)
    lock.release()
    result_q.put("hold:released")


def _try_acquire_worker(path_str, result_q, tag):
    """Child: attempt exactly one acquire; report the outcome; release."""
    from app.single_instance import AlreadyRunningError, SingleInstanceLock

    lock = SingleInstanceLock(path_str)
    try:
        lock.acquire()
    except AlreadyRunningError:
        result_q.put(f"{tag}:refused")
        return
    result_q.put(f"{tag}:acquired")
    lock.release()


def test_cross_process_mutual_exclusion(lock_path: Path):
    """A real multi-process check that only one owner exists at a time.

    Uses bounded queue/join timeouts throughout, so the test can never hang.
    """
    ctx = mp.get_context("spawn")
    path_str = str(lock_path)
    acquired_evt = ctx.Event()
    release_evt = ctx.Event()
    result_q = ctx.Queue()

    holder = ctx.Process(
        target=_hold_lock_worker,
        args=(path_str, acquired_evt, release_evt, result_q),
    )
    contenders: list = []
    procs = [holder]
    try:
        holder.start()
        # A must genuinely own the lock before anyone else tries.
        assert acquired_evt.wait(timeout=_PROC_TIMEOUT), "holder never acquired"
        assert _drain_one(result_q) == "hold:acquired"

        # While A holds, two other processes must BOTH be refused: no second
        # owner may exist concurrently.
        for tag in ("B", "C"):
            p = ctx.Process(
                target=_try_acquire_worker, args=(path_str, result_q, tag)
            )
            contenders.append(p)
            procs.append(p)
            p.start()
        outcomes = {_drain_one(result_q) for _ in contenders}
        assert outcomes == {"B:refused", "C:refused"}
        for p in contenders:
            p.join(timeout=_PROC_TIMEOUT)
            assert not p.is_alive()

        # Release A; a fresh process must now be able to acquire.
        release_evt.set()
        assert _drain_one(result_q) == "hold:released"
        holder.join(timeout=_PROC_TIMEOUT)
        assert not holder.is_alive()

        winner = ctx.Process(
            target=_try_acquire_worker, args=(path_str, result_q, "D")
        )
        procs.append(winner)
        winner.start()
        assert _drain_one(result_q) == "D:acquired"
        winner.join(timeout=_PROC_TIMEOUT)
        assert not winner.is_alive()
    finally:
        for p in procs:
            if p.is_alive():
                p.terminate()
                p.join(timeout=5)
