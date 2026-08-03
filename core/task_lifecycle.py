"""One place that decides what a finished run does to its queue row (MIR-039).

The queue had two consumers that disagreed about the same question. The CLI /
campaign path (:func:`core.autonomous_runtime.AutonomousRuntime.run_task_queue`)
branched on the run status and called ``mark_done`` or ``mark_failed``. The cron
path (``agent_tick.run_tick``) called ``mark_done`` **unconditionally** — a run
that failed, was stopped by the budget circuit, or was blocked awaiting approval
was all recorded as "done". Worse, ``runtime.run()`` was not wrapped per task, so
an exception escaped to the outer handler, which wrote a heartbeat and returned
without touching the row: the task stayed ``running`` forever, and nothing in the
live system ever recovered it (MIR-040).

Two consumers, two mappings, and the honest one only reachable from the CLI. So
the mapping lives here now and both call it:

===========================  =============================================
runtime outcome              queue status
===========================  =============================================
``completed``                ``done``
``blocked`` (needs approval) ``blocked`` — its own resting state, no retry
``stopped`` (budget/circuit) ``failed`` carrying ``stop_reason``
any other status             ``failed`` carrying the status
exception out of ``run()``   ``failed`` carrying the exception
process death mid-run        recovered at startup, under the lock, below
===========================  =============================================

``dry_run`` deliberately does **not** change the mapping. A dry-run pass that
*completed* is genuinely done — it analysed what it set out to analyse. The
honest question of what the work established is a separate axis the tick already
logs as ``result_status``; conflating the two is what let "the run crashed" and
"the analysis found nothing to fix" share one word.

Nothing here imports from ``app/`` or ``cli/``: :func:`recover_orphaned_tasks`
takes a duck-typed lock (anything exposing ``held``), so the core keeps deciding
and the entry point keeps owning the process-level machinery.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Self

from core.task_queue import RuntimeTask, TaskQueueStore

TaskOutcome = Literal["done", "blocked", "failed"]

#: Default liveness window. A task whose heartbeat has not been refreshed for
#: this long is treated as orphaned. Generous on purpose: the cost of waiting
#: is a delayed retry, the cost of being wrong is running the same work twice.
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
    *, status: str, stop_reason: str = ""
) -> LifecycleDecision:
    """Map an :class:`~core.autonomous_runtime.AutonomousRunReport` to a status.

    Pure: no store, no I/O. The table in the module docstring is this function.
    """
    status_s = (status or "").strip().lower()
    reason = (stop_reason or "").strip()
    if status_s == "completed":
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
) -> tuple[RuntimeTask, LifecycleDecision]:
    """Write the decided status for a run that returned."""
    decision = classify_run_outcome(status=status, stop_reason=stop_reason)
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
    """Write the decided status for a run that raised.

    An exception is a failure, not a mystery: the task must leave ``running``
    on this path too, or it stays claimed forever and only startup recovery can
    free it.
    """
    decision = LifecycleDecision("failed", f"{type(exc).__name__}: {exc}")
    return store.mark_failed(task_id, error=decision.reason), decision


class task_heartbeat:
    """Refresh a running task's liveness while the work happens.

    ``updated_at`` is written once when the task is claimed and then stands
    still, so a task legitimately running for an hour looks exactly like one
    whose process was killed. That ambiguity is why the first attempt at
    automatic recovery could double-run live work. A heartbeat removes it: a
    daemon thread touches the row on an interval, and it stops the instant the
    process dies — which is precisely the condition recovery wants to detect.

    Best-effort by construction. A heartbeat that cannot be written must never
    take down the run it is only observing; the consequence of a missed write is
    a task that recovery may reclaim later, not a lost result.
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
                    except Exception:  # noqa: BLE001
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
) -> list[RuntimeTask]:
    """Finalise tasks abandoned by a dead process — startup only, under a lock.

    ``lock`` is any object exposing a truthy ``held`` attribute (in production,
    :class:`app.single_instance.SingleInstanceLock`). It is not decoration: with
    the lock held, no other consumer of this queue is running, so a ``running``
    row with a stale heartbeat cannot be live work. Without it, "stale" and
    "slow" are the same observation and reclaiming the row starts a second
    execution of the same task — the hazard that got the previous attempt at
    this wiring reverted.

    Raises ``RuntimeError`` rather than recovering unlocked. A recovery that
    silently degrades to unsafe is worse than no recovery: the previous
    behaviour merely stranded tasks.
    """
    if not getattr(lock, "held", False):
        raise RuntimeError(
            "recover_orphaned_tasks requires the single-instance lock to be "
            "held; recovering while another consumer may hold a task in flight "
            "can run the same task twice"
        )
    return store.recover_stuck(timeout_minutes=timeout_minutes)
