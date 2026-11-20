"""One place that decides what a finished run does to its queue row.

| runtime outcome | queue status |
| ``completed`` | ``done`` |
| ``blocked`` (needs approval) | ``blocked`` (own resting state, no retry) |
| ``stopped`` (budget/circuit) | ``failed`` carrying ``stop_reason`` |
| any other status | ``failed`` carrying the status |
| exception out of ``run()`` | ``failed`` carrying the exception |
| process death mid-run | recovered at startup, under the lock |

``dry_run`` does not change the mapping (a completed dry-run is done; what the work established is a separate axis, ``result_status``). Nothing here imports from ``app/`` or ``cli/``: :func:`recover_orphaned_tasks` takes a duck-typed lock (anything exposing ``held``).
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Self

from core.task_queue import RuntimeTask, TaskQueueStore

TaskOutcome = Literal["done", "blocked", "failed"]

#: Default liveness window: a task whose heartbeat is not refreshed this long is treated as orphaned; generous on purpose (waiting costs a delayed retry, being wrong runs the same work twice).
DEFAULT_ORPHAN_TIMEOUT_MINUTES = 30

#: How often the consumer refreshes a running task's heartbeat.
DEFAULT_HEARTBEAT_SECONDS = 60.0


@dataclass(frozen=True)
class LifecycleDecision:
    """What one finished run means for its queue row. Pure data."""

    outcome: TaskOutcome
    reason: str

    def to_log_payload(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "reason": self.reason}


def classify_run_outcome(
    *, status: str, stop_reason: str = "", work_done: bool | None = None
) -> LifecycleDecision:
    """Map an :class:`~core.autonomous_runtime.AutonomousRunReport` to a status.

    Pure: no store, no I/O; the table in the module docstring is this function. ``work_done`` is the run's semantic verdict (`semantic_result()[1]`): a run that completed its queue without doing work is not ``done``. ``None`` keeps the lifecycle-only reading.
    """
    status_s = (status or "").strip().lower()
    reason = (stop_reason or "").strip()
    if status_s == "completed":
        if work_done is False:
            return LifecycleDecision("failed", "run completed without work")
        return LifecycleDecision("done", "run completed")
    if status_s == "blocked":
        return LifecycleDecision(
            "blocked", reason or "approval required"
        )
    if status_s == "stopped":
        return LifecycleDecision(
            "failed", reason or "stopped by budget or circuit"
        )
    return LifecycleDecision(
        "failed", reason or f"run status={status_s or 'unknown'}"
    )


def apply_run_outcome(
    store: TaskQueueStore,
    task_id: str,
    *,
    status: str,
    stop_reason: str = "",
    report: dict | None = None,
    work_done: bool | None = None,
) -> tuple[RuntimeTask, LifecycleDecision]:
    """Write the decided status for a run that returned."""
    decision = classify_run_outcome(
        status=status, stop_reason=stop_reason, work_done=work_done,
    )
    if decision.outcome == "done":
        return store.mark_done(task_id, report=report), decision
    if decision.outcome == "blocked":
        return (
            store.mark_blocked(task_id, reason=decision.reason, report=report),
            decision,
        )
    return (
        store.mark_failed(task_id, error=decision.reason, report=report),
        decision,
    )


def apply_run_exception(
    store: TaskQueueStore,
    task_id: str,
    exc: BaseException,
) -> tuple[RuntimeTask, LifecycleDecision]:
    """Write the decided status for a run that raised; the task must leave ``running`` on this path too, or only startup recovery can free it."""
    decision = LifecycleDecision("failed", f"{type(exc).__name__}: {exc}")
    return store.mark_failed(task_id, error=decision.reason), decision


class task_heartbeat:
    """Refresh a running task's liveness while the work happens.

    Best-effort: a heartbeat that cannot be written must never take down the run it observes; a missed write only means recovery may reclaim the task later.
    """

    def __init__(
        self,
        store: TaskQueueStore,
        task_id: str,
        *,
        interval_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        self._store = store
        self._task_id = task_id
        self._interval = max(1.0, float(interval_seconds))
        self._on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.beats = 0

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                if self._store.heartbeat(self._task_id) is None:
                    return  # task is no longer running; nothing to keep alive
                self.beats += 1
            except Exception as exc:  # noqa: BLE001
                if self._on_error is not None:
                    try:
                        self._on_error(exc)
                    except Exception:  # noqa: BLE001, S110 — the error reporter must not raise over the error
                        pass

    def __enter__(self) -> Self:
        self._thread = threading.Thread(
            target=self._run,
            name=f"task-heartbeat-{self._task_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=5.0)


def recover_orphaned_tasks(
    store: TaskQueueStore,
    *,
    lock: Any,
    timeout_minutes: int = DEFAULT_ORPHAN_TIMEOUT_MINUTES,
    finalise_exhausted: bool = True,
) -> list[RuntimeTask]:
    """Finalise tasks abandoned by a dead process; startup only, under a lock.

    ``finalise_exhausted=False`` is the dry caller's word: re-queue what can be, leave the terminal verdict to a pass that applies effects.
    """
    if not getattr(lock, "held", False):
        raise RuntimeError(
            "recover_orphaned_tasks requires the single-instance lock to be "
            "held; recovering while another consumer may hold a task in flight "
            "can run the same task twice"
        )
    return store.recover_stuck(
        timeout_minutes=timeout_minutes,
        finalise_exhausted=finalise_exhausted,
    )


#: How long a budget-parked checkpoint waits before being offered again: the gated budget window is hourly and these rows carry a single attempt, so returning one while the window is dry would spend its only attempt on a run that cannot finish.
DEFAULT_RESUME_COOLDOWN_MINUTES = 60


def reactivate_resumable_work(
    store: TaskQueueStore,
    *,
    lock: Any,
    cooldown_minutes: int = DEFAULT_RESUME_COOLDOWN_MINUTES,
) -> list[RuntimeTask]:
    """Return budget-parked checkpoints to the queue; startup only, under the lock.

    Sibling of :func:`recover_orphaned_tasks`: that one handles rows abandoned by a dead process, this one rows parked by an exhausted resource (which had no exit). Same lock contract: reactivating a row a consumer holds in flight could run work twice.
    """
    if not getattr(lock, "held", False):
        raise RuntimeError(
            "reactivate_resumable_work requires the single-instance lock to be "
            "held; returning a paused row to the queue while another consumer "
            "may hold it in flight can run the same work twice"
        )
    return store.reactivate_paused_checkpoints(cooldown_minutes=cooldown_minutes)
