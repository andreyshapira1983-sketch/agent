# Daemon implementation progress

Tracker for the incremental asyncio-daemon plan. One sub-item per run, one PR
per sub-item. `agent_tick.py` stays as the single-shot fallback mode throughout.

## 1.1 Main async event loop

- **Status:** ready for review
- **Branch:** `andreyshapira1983-sketch-daemon-1-1-async-event-loop`
- **Pull Request:** (see PR titled "Daemon 1.1: add async event loop")
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

## Remaining sub-items

| Item | Title | Status |
| --- | --- | --- |
| 1.2 | Lifecycle and graceful shutdown | not started |
| 1.3 | Single-instance guarantee | not started |
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
