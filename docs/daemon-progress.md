# Daemon implementation progress

Tracker for the incremental asyncio-daemon plan. One sub-item per run, one PR
per sub-item. `agent_tick.py` stays as the single-shot fallback mode throughout.

## 1.1 Main async event loop

- **Status:** completed
- **Branch:** `andreyshapira1983-sketch-daemon-1-1-async-event-loop`
- **Pull Request:** #35 (merged)
- **Last updated:** 2026-07-10
- **Implementation:** New `app/daemon.py` with `DaemonLoop` — a persistent
  asyncio loop that sleeps on an internal `asyncio.Event`, wakes via
  `wake(reason)` / `wake_threadsafe(reason)`, batches accumulated reasons per
  iteration, dispatches them to an async `on_wake` handler, and exits only via
  `request_stop()`. Optional `idle_timeout` provides the hook for future timer
  sources (2.1). Handler exceptions are logged and do not kill the loop;
  cancellation propagates (hook for graceful shutdown, 1.2). One instance runs
  at most once (`DaemonLoopError` on re-run/restart).
- **Tests added:** `tests/test_daemon_loop.py` (13 tests): persistence across
  passes, stop-before-wake, idempotent stop, reason batching, thread-safe wake
  (incl. before start), handler error survival, idle timeout, invalid config,
  concurrent/re-run guard, cancellation, wake-after-stop. All use bounded
  timeouts; no real long sleeps.
- **Checks run:**
  - `python -m pytest tests/test_daemon_loop.py -q` → 13 passed
  - `python -m pytest -q` (full suite) → see PR description
  - `coverage run --branch -m pytest && coverage report --fail-under=85` → see PR description
- **Known limitations:** No signal handling, no task draining, no
  single-instance lock, no CLI entry point — those are sub-items 1.2/1.3.
- **Blockers:** none. **Human action:** review and merge the PR.

## 1.2 Lifecycle and graceful shutdown

- **Status:** ready for review
- **Branch:** `andreyshapira1983-sketch-daemon-1-2-graceful-shutdown`
- **Pull Request:** (see PR titled "Daemon 1.2: lifecycle and graceful shutdown")
- **Last updated:** 2026-07-10
- **Implementation:** Extended `app/daemon.py`:
  - `spawn(coro)` starts and tracks in-flight tasks; refuses new tasks once
    shutdown has begun (`DaemonLoopError`).
  - `shutdown(drain_timeout=…)` — idempotent, concurrent-safe graceful stop:
    requests loop exit, drains tracked tasks for a bounded `drain_timeout`
    (constructor default 10 s), cancels stragglers with a bounded
    `CANCEL_GRACE_SECONDS` grace, then runs registered close callbacks
    (`add_close_callback`, sync or async; errors logged, never swallowed
    silently). Repeated / concurrent shutdown calls all return safely.
  - `run(handle_signals=True)` installs Ctrl+C (SIGINT) and SIGTERM handlers
    via `loop.add_signal_handler` on Unix, falling back to `signal.signal`
    where the loop API is unsupported (Windows Proactor); handlers are
    restored on exit. Finalization runs exactly once in `run()`'s `finally`,
    so cancellation of `run()` also drains and closes resources.
  - Observability: `shutting_down`, `active_tasks` properties.
- **Tests added:** `tests/test_daemon_shutdown.py` (16 tests): task
  tracking/untracking, spawn refusal during shutdown, drain of in-flight
  task, cancellation past drain timeout, failing task survival, repeated and
  concurrent shutdown, shutdown without run, close-callback order and error
  isolation, signal callback + fallback paths, handler install/restore,
  invalid timeouts, wake-after-shutdown ignored. Bounded timeouts throughout;
  no real signals sent, no long sleeps.
- **Checks run:**
  - `python -m pytest tests/test_daemon_shutdown.py tests/test_daemon_loop.py -q` → 29 passed
  - `coverage run --branch -m pytest` → 4205 passed
  - `coverage report --fail-under=85` → TOTAL 92%
  - `python scripts/generate_sbom.py --check` → in sync
  - `python scripts/audit_release.py` → no warnings
  - pylint: not installed in the local environment and not part of CI; skipped.
- **Known limitations:** SIGTERM is defined but effectively never delivered
  on Windows; Ctrl+C uses the `signal.signal` fallback there. No
  single-instance lock or CLI entry point yet (sub-items 1.3/1.4).
- **Blockers:** none. **Human action:** review and merge the PR.

## 1.3 Single-instance guarantee

- **Status:** ready for review
- **Branch:** `andreyshapira1983-sketch-daemon-1-3-single-instance` (feature),
  `fix/single-instance-release-race` (hotfix)
- **Pull Request:** #37 (merged); hotfix PR (see PR titled
  "Hotfix: single-instance release() must not unlink the lock file")
- **Last updated:** 2026-07-10
- **Hotfix (release() race):** The original `release()` unlinked
  `data/daemon.lock` after dropping the OS lock. On POSIX this is racy: the
  lock lives on the open file *description* (inode), not the pathname, so
  between our unlock and unlink a second process could open the same file and
  lock the inode, our unlink would then remove the name, and a third process
  could create a fresh `daemon.lock` and acquire a *second, simultaneous*
  lock — two live owners at once. Fix: `release()` now only drops the OS lock,
  closes the handle, and clears internal state; it **never** unlinks the file.
  `data/daemon.lock` is a permanent service artefact; a leftover, unlocked file
  is the normal resting state and the next `acquire()` re-locks it and rewrites
  the diagnostics (pid, hostname, started_at). The OS lock — not the file's
  presence — is the single source of truth.
- **Implementation:** New `app/single_instance.py` with `SingleInstanceLock`
  and `AlreadyRunningError`. The lock is backed by an OS advisory exclusive
  lock on an open file descriptor — `fcntl.flock` on POSIX, `msvcrt.locking`
  on Windows — reusing the project idiom from `core/file_lock.py`. Because the
  OS releases the lock automatically when the holder exits (including a crash
  or `kill -9`), a *stale* lock left by a crashed process is acquired
  transparently with no racy PID-liveness checks. The file additionally stores
  small JSON diagnostics (pid, hostname, started_at) so the "already running"
  error message and operators can identify the live holder; those are advisory
  only — correctness comes from the OS lock. On Windows, which uses *mandatory*
  byte-range locking, the lock byte is placed far past the payload
  (`_WINDOWS_LOCK_OFFSET`) so the diagnostics at offset 0 stay readable.
  `acquire()`/`release()` are idempotent; `release()` only removes the file it
  owns, so it can never delete a lock held by another live process. Usable as a
  context manager. `agent_tick.py` is untouched and needs no lock.
- **Tests added:** `tests/test_single_instance.py` (17 tests): acquire/release
  basics, relative default path, mutual exclusion, holder pid/hostname in the
  error, re-acquire after release, idempotent acquire/release, release without
  acquire, stale-lock recovery (simulated crash), corrupt-file graceful
  degradation, context manager (incl. refused nested instance), restart loop,
  a bystander object not deleting a live lock, diagnostics rewritten on each
  fresh acquire, and a **real multiprocessing** mutual-exclusion test (holder
  process A refuses concurrent B and C, then a fresh process D wins after A
  releases) with bounded queue/join timeouts so it can never hang. After the
  hotfix, release keeps the file: tests assert `held is False` while the file
  may remain and a new lock still acquires successfully.
- **Checks run:**
  - `python -m pytest tests/test_single_instance.py -q` → 17 passed
  - `coverage run --branch -m pytest` → see PR description
  - `coverage report --fail-under=85` → see PR description
  - `python scripts/generate_sbom.py --check` → see PR description
  - `python scripts/audit_release.py` → see PR description
- **Known limitations:** No CLI entry point or daemon wiring yet — the lock is
  a standalone building block a future runner (1.4) will wrap around
  `DaemonLoop.run`. Windows SIGTERM caveats from 1.2 are unchanged.
- **Blockers:** none. **Human action:** review and merge the PR.

## Remaining sub-items

| Item | Title | Status |
| --- | --- | --- |
| 1.2 | Lifecycle and graceful shutdown | completed |
| 1.3 | Single-instance guarantee | ready for review |
| 1.4 | Windows service launch | not started |
| 2.1 | Timer events (in-loop scheduler) | not started |
| 2.2 | File watcher | not started |
| 2.3 | Instant wake on new RuntimeTask | not started |
| 2.4 | External events | skipped (explicitly deferred) |
| 3.1 | Priority event queue | not started |
| 3.2 | Worker pool | not started |
| 3.3 | Task timeout and cancellation | not started |
| 3.4 | Event deduplication | not started |
| 4.1 | In-flight task checkpointing | not started |
| 4.2 | Recovery on start | not started |
| 4.3 | Circuit breaker | not started |
| 4.4 | Heartbeat and watchdog | not started |
| 5.1 | Budget and kill-switch integration | not started |
| 5.2 | PolicyGate and approval | not started |
| 5.3 | Quiet hours and work windows | not started |
| 6.1 | Structured logs | not started |
| 6.2 | Daemon control commands | not started |
| 6.3 | Metrics | not started |
| 7.1 | Event loop unit tests | not started |
| 7.2 | Integration test | not started |
| 7.3 | agent_tick.py compatibility | not started |
| 7.4 | Gradual rollout (dry-run) | not started |
