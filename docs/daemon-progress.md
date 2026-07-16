# Daemon implementation progress

Tracker for the incremental asyncio-daemon plan. One sub-item per run, one PR
per sub-item. `agent_tick.py` stays as the single-shot fallback mode throughout.

Each sub-item reports four independent fields so the status is unambiguous:

- **implementation** — is the code for the sub-item done (`completed` / `partial` / `not started`)
- **main_pr** — state of the primary PR (`merged` / `open` / `none`)
- **hotfix** — state of any follow-up fix PR (`merged` / `awaiting_review` / `none`)
- **acceptance** — has the sub-item been verified as accepted per the plan's
  Definition of Done (`accepted` / `pending`)

## 1.1 Main async event loop

- **implementation:** completed | **main_pr:** #35 merged | **hotfix:** none | **acceptance:** pending
- **Branch:** `andreyshapira1983-sketch-daemon-1-1-async-event-loop`
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
- **Blockers:** none. **Human action:** none -- PR merged; plan acceptance (Definition of Done sign-off) still pending.

## 1.2 Lifecycle and graceful shutdown

- **implementation:** completed | **main_pr:** #36 merged | **hotfix:** none | **acceptance:** pending
- **Branch:** `andreyshapira1983-sketch-daemon-1-2-graceful-shutdown`
- **Pull Request:** #36 (merged)
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
- **Blockers:** none. **Human action:** none -- PR merged; plan acceptance (Definition of Done sign-off) still pending.

## 1.4 Windows service shell (architecture-only)

- **implementation:** completed | **main_pr:** #40 merged | **hotfix:** none | **acceptance:** pending
- **Branch:** `codex/daemon-1-4-windows-service-shell`
- **Pull Request:** #40 (merged)
- **Last updated:** 2026-07-10
- **Implementation:** New `app/windows_service.py` fixing the Windows service
  *contract* around the single selected mechanism (`pywin32`) without yet
  installing, removing, starting, stopping, or configuring recovery for a real
  service -- those are deferred to later roadmap items and are intentionally not
  implemented here. `WindowsServiceContract` (frozen dataclass) resolves all
  machine-local paths at runtime (workspace, `.venv` interpreter, `data/`,
  `logs/`, launch command `python -m app.daemon`) so no absolute machine path
  is baked into the repo; `from_environment()` applies optional non-secret
  overrides (`AGENT_SERVICE_WORKSPACE`, `AGENT_SERVICE_VENV`,
  `AGENT_SERVICE_ACCOUNT`, `AGENT_SERVICE_STOP_TIMEOUT_SECONDS`).
  `validate_contract()` checks OS-independent invariants; `validate_runtime()`
  additionally verifies the host is Windows, pywin32 modules are importable, and
  the workspace/interpreter exist -- all read-only, never mutating the service.
  `as_dict()` exposes an honest shell-only contract (every
  `*_implemented` flag is `False`). The module is import-safe on any OS:
  `pywin32` is never imported at module import time; a small CLI
  (`--show-contract` / `--check-runtime`) prints the contract or checks
  prerequisites. `agent_tick.py` is untouched.
- **Tests added:** `tests/test_windows_service.py` (26 tests): import safety
  (no pywin32 pulled in), default contract shape, repo-root workspace not
  hardcoded, workspace-relative derived paths, explicit venv override,
  `from_environment` defaults + overrides + bad/zero timeout rejection,
  `validate_contract` rejection matrix, JSON-serialisable shell-only `as_dict`,
  `validate_runtime` error paths (off-Windows, missing pywin32, missing
  workspace, missing interpreter) via monkeypatched `os.name`/`find_spec` with
  no real service call, and full CLI coverage (show-contract, default, unknown
  arg -> usage/2, off-Windows check -> 1, simulated success path, config error
  -> 1). Module coverage 98%.
- **Checks run:**
  - `python -m pytest tests/test_windows_service.py -q` -> 26 passed
  - `coverage run --branch -m pytest` -> 4251 passed
  - `coverage report --fail-under=85` -> TOTAL 92%
  - `python scripts/generate_sbom.py --check` -> in sync
  - `python scripts/audit_release.py` -> no warnings
- **Known limitations:** No real service install/uninstall/start/stop or Windows
  recovery configuration yet -- this sub-item deliberately fixes only the
  contract/shell. Those actions are separate later roadmap tasks.
- **Blockers:** none. **Human action:** none -- PR merged; plan acceptance (Definition of Done sign-off) still pending.

## 1.3 Single-instance guarantee

- **implementation:** completed | **main_pr:** #37 merged | **hotfix:** #38 merged | **acceptance:** pending
- **Branch:** `andreyshapira1983-sketch-daemon-1-3-single-instance` (feature),
  `fix/single-instance-release-race` (hotfix)
- **Pull Request:** #37 (merged); hotfix #38 (merged)
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
- **Blockers:** none. **Human action:** none -- PR merged; plan acceptance (Definition of Done sign-off) still pending.

## 2.1 Timer events (in-loop scheduler)

- **implementation:** completed | **main_pr:** #44 merged | **hotfix:** none | **acceptance:** pending
- **Branch:** `andreyshapira1983-sketch-daemon-2-1-timer-events`
- **Pull Request:** #44 (merged)
- **Last updated:** 2026-07-10
- **Implementation:** Added `SchedulerService` to `core/scheduler.py` — an
  async component that drives the existing `SchedulerStore.tick` from *inside*
  the asyncio event loop instead of from an external cron. Each iteration
  computes the exact seconds until the earliest active schedule is due
  (`seconds_until_next`), sleeps precisely that long (clamped to a bounded
  `idle_interval`, default 1 h, when nothing is pending) and only then ticks —
  no busy polling. The wait is cooperative: `notify()` shortens it the moment a
  schedule is added/changed, `stop()` ends the loop, and task cancellation
  propagates so the daemon's graceful shutdown (1.2) can drain/cancel it. Time
  is fully injectable (`now` + `sleep`) so tests use a fake clock rather than
  real sleeps. An optional `on_tick` callback (sync or async; errors logged,
  never swallowed silently) is the hook the daemon loop uses to `wake` its
  dispatcher. `SchedulerStore`, the persisted schedule format, and every
  existing schedule are unchanged (backward compatible). `agent_tick.py` and
  its single-shot path are untouched — the service is opt-in and not wired into
  any entry point in this sub-item.
- **Tests added:** `tests/test_scheduler_service.py` (22 tests) using a
  `FakeClock` (fake `now` + instant `sleep` that advances the clock):
  construction validation (idle_interval/limit), `seconds_until_next`
  (empty/future/overdue/paused), single due-tick + next-run advance, multi-period
  firing over simulated time, async `on_tick` awaited, `on_tick` error survival,
  `limit` forwarding, instant wake via `notify` beating a 30 s sleep,
  sleep-elapsed vs woken return values, pre-set stop, stop-before-run (no tick),
  cancellation propagation clearing `running`, run-twice guard, zero-enqueue
  recovery without hot-spin (out-of-band schedule removal), no-callback run, a
  cancellation-in-callback path, and observability properties. All bounded by a
  5 s `run_async` timeout; no real long sleeps, no leftover tasks.
- **Checks run:**
  - `python -m pytest tests/test_scheduler_service.py tests/test_scheduler.py -q` → 29 passed
  - `coverage run --branch -m pytest` → 4273 passed
  - `coverage report --fail-under=85` → TOTAL 92%
  - `coverage report --include=core/scheduler.py` → `SchedulerService` fully
    covered (remaining misses are pre-existing `SchedulerStore` lines exercised
    elsewhere)
  - `python scripts/generate_sbom.py --check` → in sync
  - `python scripts/audit_release.py` → no warnings
  - pylint/pyflakes: not installed locally and not part of CI; skipped.
- **Known limitations:** The service is a standalone building block; it is not
  yet spawned by `DaemonLoop.run` (that wiring lands with the worker/dispatcher
  sub-items). `SchedulerStore.tick` still does brief synchronous local-file I/O
  inline on the loop thread; it is fast and deterministic, but a future
  sub-item may move it to an executor if profiling warrants. No external-cron
  removal beyond providing the in-loop replacement.
- **Blockers:** none. **Human action:** none -- PR #44 merged; plan acceptance (Definition of Done sign-off) still pending.

## 2.2 File watcher

- **implementation:** completed | **main_pr:** #45 merged | **hotfix:** none | **acceptance:** pending
- **Branch:** `andreyshapira1983-sketch-daemon-2-2-file-watcher` (created via the
  branch-rename tool; the tool prefixed/truncated the stored ref, but the PR
  title and this entry are the source of truth for the sub-item mapping)
- **Pull Request:** #45 (merged)
- **Last updated:** 2026-07-11
- **Implementation:** New `app/file_watcher.py` with `FileWatcher` — the second
  daemon event source (after the 2.1 timer scheduler). It watches a set of files
  and/or directories (e.g. the approval inbox and applicable config files) and
  emits a coalesced batch of `FileChange(kind, path)` (created/modified/deleted)
  whenever the watched set changes, giving the daemon loop a `wake` hook so a
  human dropping an approval or editing config is picked up without waiting for
  the next scheduler tick. Design decisions matching the plan and repo idioms:
  - **No heavy dependency.** Instead of pulling in `watchdog` (a native,
    platform-specific dependency needing lock-file/SBOM churn and justification),
    it uses a lightweight `stat`-snapshot diff over a cooperative poll — each poll
    is a cheap `stat` of a small known path set and yields the loop between polls,
    so it never blocks the loop and never busy-spins. Time is fully injectable
    (`now` + `sleep`), the same pattern as `SchedulerService`.
  - **Debounced / coalesced.** A burst of rapid writes collapses into a single
    batch: the watcher only emits once the set has been quiet for a bounded
    `debounce` window (`created`+`modified` coalesce to `created`;
    `created`+`deleted` cancels out).
  - **Survives a missing directory.** A watched path that does not exist yet is
    treated as empty; when it appears its entries are reported as created. A path
    that vanishes mid-scan is treated as absent, never an error.
  - **No self-echo.** The watcher only ever reads (`stat`); it never writes to the
    paths it watches, and unchanged signatures produce no event.
  - Cooperates with the daemon loop rather than owning it: `stop()` / `notify()`
    mirror `SchedulerService`, cancellation propagates for graceful shutdown, and
    a settled pending batch is flushed on clean exit. `agent_tick.py` is untouched
    and does not import this module; the watcher is a standalone opt-in building
    block not yet wired into any entry point.
- **Tests added:** `tests/test_file_watcher.py` (27 tests) using a `StepClock`
  (fake `now` + instant `sleep` that advances the clock and runs a per-poll hook
  so files are mutated deterministically between polls): construction/validation
  (empty paths, non-positive poll_interval, negative debounce, exposed defaults),
  pure `scan`/`_diff`/`_merge_pending` logic (directory listing, pattern filter,
  missing target, created/modified/deleted diff, created+deleted cancel,
  created+modified stays created), and async behaviour — new file, modified file,
  deleted file, burst coalesced into one batch, stable set emits nothing (no
  self-echo), missing-directory tolerance then later creation, `emit_existing`
  baseline, recursive nested detection, callback error survival, run-twice guard,
  cancellation propagation, stop-before-run short-circuit, `notify` early-wake,
  idempotent stop, flush-pending-on-stop before debounce, and no-callback run.
  All bounded by a 5 s `run_async` timeout; no real long sleeps, no leftover tasks.
- **Checks run:**
  - `python -m pytest tests/test_file_watcher.py -q` → 27 passed
  - `coverage run --branch -m pytest` → 4300 passed
  - `coverage report --fail-under=85` → TOTAL 92%
  - `coverage report -m --include=app/file_watcher.py` → 94% (remaining misses are
    defensive `stat`-race / cancellation branches)
  - `python scripts/generate_sbom.py --check` → in sync
  - `python scripts/audit_release.py` → no warnings
  - `pip check` equivalent / lint: CI has no lint/format/type gates (only tests,
    pip-check, SBOM, audit, coverage); all local equivalents pass. pylint not
    installed locally and not part of CI; skipped.
- **Known limitations:** The watcher is a standalone building block; it is not yet
  spawned by `DaemonLoop.run` (that wiring lands with the worker/dispatcher
  sub-items). It is poll-based (bounded `stat` diff), not kernel-notification
  based; the default 1 s cadence is a deliberate trade-off avoiding a native
  dependency and is adequate for the approval-inbox / config use case. Signatures
  use `(mtime_ns, size)`, so an in-place edit that preserves both would be missed
  — not a concern for append-only JSONL inboxes or config edits, and a future
  sub-item can add a content hash if needed.
- **Blockers:** none. **Human action:** optional read-only acceptance review;
  do not reimplement.

## 2.3 Instant wake on new RuntimeTask

- **implementation:** completed | **main_pr:** #83 merged | **hotfix:** none | **acceptance:** pending
- **Branch:** `daemon/2.3-runtime-task-wake`
- **Pull Request:** #83 (merged)
- **Last updated:** 2026-07-12
- **Implementation:** Extended the existing TaskQueueStore with an optional
  on_task_added(RuntimeTask) callback. Both normal tasks and paused checkpoint
  tasks invoke it immediately after the JSONL rewrite succeeds and after the
  queue file lock is released. A daemon-owned store can pass
  DaemonLoop.wake_threadsafe (including a task-id reason), so in-process
  producers wake the persistent loop without polling or a second queue.
  Callback failures are logged and isolated: the already-durable task remains
  available for the next wake/recovery pass. Existing constructors, persisted
  RuntimeTask data, ordering, scheduler behaviour, and agent_tick.py are
  unchanged.
- **Tests added:** Four deterministic cases in tests/test_task_queue.py:
  callback-after-persistence (also proves it runs outside the file lock), paused
  checkpoint notification, callback-error durability/logging, and a bounded
  integration test showing a new task wakes DaemonLoop through wake_threadsafe
  and supplies the exact task-id reason. No real sleeps are used.
- **Checks run:** See merged PR #83 / GitHub CI (pytest + coverage).
- **Known limitations:** The callback is an in-process wake bridge. A task
  written by another process uses the 2.2 file-watcher path until daemon
  composition/wiring lands with the dispatcher/worker items. The callback is
  synchronous by design because DaemonLoop.wake_threadsafe is synchronous.
  No daemon entry point is introduced here.
- **Blockers:** none. **Human action:** optional read-only acceptance review;
  do not reimplement.

## 3.1 Priority event queue

- **implementation:** completed | **main_pr:** #84 merged | **hotfix:** none | **acceptance:** pending
- **Branch:** `daemon/3.1-priority-event-queue`
- **Pull Request:** #84 (merged)
- **Last updated:** 2026-07-12
- **Implementation:** New `app/priority_event_queue.py` with typed
  `DaemonEvent`, `EventPriority` (`urgent > scheduled > background`), and
  `PriorityEventQueue`. Same-priority order is stable FIFO via a monotonic
  sequence. Anti-starvation: after `aging_after` consecutive higher-priority
  pops (default 8; `0` disables), a waiting background event is served next.
  Optional `on_put` can call `DaemonLoop.wake` / `wake_threadsafe`. `close()`
  refuses further puts and unblocks async `get` waiters. Does not run workers
  (3.2), does not own the daemon loop, does not change `agent_tick.py`.
- **Reused:** `core.ids.new_id`, `DaemonLoop.wake_threadsafe` in the wake
  integration test, logging isolation pattern from `TaskQueueStore` callbacks.
- **Tests added:** `tests/test_priority_event_queue.py` — coerce/validation,
  priority + FIFO order, aging vs strict priority, on_put error isolation,
  DaemonLoop wake bridge, async get/close/cancel paths, repeated close.
- **Checks run:** See merged PR #84 / GitHub CI.
- **Known limitations:** In-memory only (no durable priority queue across
  process restart — recovery remains 4.2). Not yet wired as the daemon's sole
  dispatcher; producers still wake via reason strings until composition lands.
- **Blockers:** none. **Human action:** optional read-only acceptance review;
  do not reimplement.

## 3.2 Worker pool

- **implementation:** completed | **main_pr:** #86 merged | **hotfix:** none | **acceptance:** pending
- **Branch:** `daemon/3.2-worker-pool`
- **Pull Request:** #86 (merged)
- **Last updated:** 2026-07-13
- **Implementation:** New `app/worker_pool.py` with `WorkerPool` consuming an
  existing `PriorityEventQueue` (no second queue). Configurable `max_workers`
  (default 2). Workers are asyncio tasks; handler exceptions are logged and
  isolated so one failure does not stop the pool. `shutdown()` closes the
  queue (stop accepting), drains in-flight handlers for a bounded
  `drain_timeout`, then cancels stragglers; repeated shutdown is safe.
  Does not own `DaemonLoop`, does not change `agent_tick.py`.
- **Reused:** `PriorityEventQueue.get` / `close` / `PriorityEventQueueClosed`
  from 3.1; shutdown/drain pattern aligned with `DaemonLoop` (1.2).
- **Tests added:** `tests/test_worker_pool.py` — defaults/validation,
  concurrency cap, error isolation, shutdown refuse+idempotent, drain of
  short work, run-cancellation → shutdown.
- **Checks run:** See merged PR #86 / GitHub CI.
- **Known limitations:** Composition into a full daemon entry point still
  pending; durable recovery remains 4.x. Per-event timeout added in 3.3.
- **Blockers:** none. **Human action:** optional read-only acceptance review;
  do not reimplement.

## 3.3 Task timeout and cancellation

- **implementation:** completed | **main_pr:** #87 merged | **hotfix:** none | **acceptance:** pending
- **Branch:** `daemon/3.3-task-timeout`
- **Pull Request:** #87 (merged)
- **Last updated:** 2026-07-13
- **Why this item:** Implemented after 3.2 (#86) as the next item in sequence.
- **Implementation:** Extended `WorkerPool` with configurable `task_timeout`
  (default 60s; `None` disables). Each handler runs under `asyncio.wait_for`;
  on timeout the awaitable is cancelled, `timeout_count` increments, success
  counter does **not**, and a warning log includes `reason=task_timeout`.
  Explicit `CancelledError` still propagates and is never counted as success.
  Added `metrics()` snapshot for later daemon-status (6.2/6.3).
- **Reused:** existing `WorkerPool` / `PriorityEventQueue` (no second executor).
- **Tests added/updated:** `tests/test_worker_pool.py` — timeout then continue,
  cancel ≠ success, `task_timeout=None` park until shutdown, validation,
  metrics counters. Bounded waits only (≤0.08s), no long real sleeps.
- **Checks run:**
  - `python -m pytest tests/test_worker_pool.py tests/test_priority_event_queue.py -q` → 25 passed
- **Also in this branch:** `.cursor/rules/daemon-plan-workflow.mdc` so agents
  keep the one-item daemon protocol without re-pasting the full checkpoint.
- **Known limitations:** No separate metrics backend yet (6.3); timeout uses
  asyncio loop time (tests use short real timeouts, not a fake clock — same
  pattern as 3.2 cancel tests).
- **Blockers:** none. **Human action:** none -- PR #87 merged; plan acceptance
  (Definition of Done sign-off) remains pending.

## 3.4 Event deduplication

- **implementation:** completed | **main_pr:** none (local work) | **hotfix:** none | **acceptance:** pending
- **Branch:** `daemon/3.4-event-deduplication` (local only)
- **Last updated:** 2026-07-16
- **Implementation:** Extended the existing in-memory `PriorityEventQueue` and
  `DaemonEvent` with an optional normalized `dedup_key`. While a keyed event is
  waiting, a repeated `put()` with the same key returns that original event and
  does not change queue size, sequence, callback count, priority, payload,
  event id, or FIFO position. Unkeyed events retain the original behaviour and
  may repeat. The key is released in the shared pop path, covering
  `get_nowait()`, `pop_batch()`, async `get()`, and the background-aging branch;
  it can then be enqueued again. Empty/whitespace keys raise `ValueError`, and
  a closed queue continues to reject every `put()`.
- **Reused:** Existing `PriorityEventQueue` heap, sequence, aging, close/wake
  semantics, and `WorkerPool.get()` consumption path; no second queue or store.
- **Tests added/updated:** `tests/test_priority_event_queue.py` covers same and
  different keys, absent keys, sequence preservation, first-event
  priority/payload/id/FIFO preservation, key reuse after sync/async get,
  `pop_batch`, background aging, one-shot `on_put`, invalid empty keys, and a
  closed queue. Existing worker-pool tests provide consumer compatibility.
- **Checks run:**
  - `python -m pytest tests/test_priority_event_queue.py tests/test_worker_pool.py -q` -> 35 passed
  - `python -m pytest -q` -> 4636 passed
  - `python -m pip check` -> no broken requirements
  - `python scripts/generate_sbom.py --check` -> in sync
  - `python scripts/audit_release.py` -> release/supply-chain checks passed;
    local forbidden artefacts exist but are excluded from release packaging
  - `python -m coverage run --branch -m pytest` -> 4636 passed
  - `python -m coverage report --fail-under=85` -> TOTAL 93%
- **Known limitations:** Deduplication is process-local and only covers events
  currently waiting in this queue. It has no persistence, TTL, restart
  recovery, database, or in-flight-handler suppression; recovery remains 4.x.
- **Blockers:** none in local implementation. **Human action:** review local
  work separately; no PR exists and acceptance remains pending.

| Item | Title | Status |
| --- | --- | --- |
| 1.1 | Main async event loop | merged (acceptance pending) |
| 1.2 | Lifecycle and graceful shutdown | merged (acceptance pending) |
| 1.3 | Single-instance guarantee | merged incl. hotfix (acceptance pending) |
| 1.4 | Windows service launch | merged (acceptance pending) |
| 2.1 | Timer events (in-loop scheduler) | merged (acceptance pending) |
| 2.2 | File watcher | merged (acceptance pending) |
| 2.3 | Instant wake on new RuntimeTask | merged (acceptance pending) |
| 2.4 | External events | skipped (explicitly deferred) |
| 3.1 | Priority event queue | merged (acceptance pending) |
| 3.2 | Worker pool | merged (acceptance pending) |
| 3.3 | Task timeout and cancellation | merged (acceptance pending) |
| 3.4 | Event deduplication | completed locally; no PR (acceptance pending) |
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
