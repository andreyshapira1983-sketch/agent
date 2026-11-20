"""Long work session skeleton (MVP-17.1): a bounded multi-cycle session of autonomous health passes that stops on ``max_cycles``, the wall-clock ``minutes`` budget, an open circuit breaker or convergence (identical outcome signature ``convergence_window`` times in a row); progress goes to stderr every ``report_every`` cycles; no side effects in dry-run (delegates to AutonomousRuntime).

Usage (CLI):
    :work-session "project health" --dry-run --minutes 10 --max-cycles 3 --report-every 1
"""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig
from core.budget_governor import BudgetLimits
from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

logger = logging.getLogger(__name__)

WorkSessionStatus = Literal["completed", "stopped", "interrupted"]

#: Investigation goal for the cycle after a run of identical endings; {signature} is the repeated signature, {goal} the shift's original goal the session returns to.
INVESTIGATION_GOAL_TEMPLATE = (
    "Investigate why goal {goal!r} keeps producing the same outcome "
    "signature {signature!r}; propose and apply a fix."
)
WorkSessionStopReason = Literal[
    "max_cycles", "time_budget", "circuit_open", "converged", "budget_exhausted",
    "awaiting_approval", "interrupted", ""
]


@dataclass(frozen=True)
class WorkSessionConfig:
    """Immutable configuration for one work session."""

    goal: str = "project health"
    # dry_run=False means real effects; AutonomousRuntime still blocks unless effects_approved=True, so False is a safe default.
    dry_run: bool = False
    minutes: float = 10.0
    max_cycles: int = 3
    report_every: int = 1
    # Convergence stop: identical outcome signatures this many times in a row while cycles remain stop the session early (stop_reason="converged"). False disables.
    stop_on_convergence: bool = True
    convergence_window: int = 3
    # Marathon heartbeat: minimum wall-clock seconds one cycle's slot occupies; after each non-final cycle the session sleeps the remainder. 0 keeps sprint behaviour.
    pace_seconds: float = 0.0
    # Investigation switch (default OFF): when a cycle's outcome signature repeats `investigation_window` times in a row, a follow-up cycle with the investigation goal template runs instead of stopping.
    investigate_on_repeat: bool = False
    # Identical signatures required before investigation fires; strictly LESS than convergence_window so it triggers before the stop-crane (see __post_init__).
    investigation_window: int = 2

    def __post_init__(self) -> None:
        if self.minutes <= 0:
            raise ValueError("minutes must be > 0")
        if self.pace_seconds < 0:
            raise ValueError("pace_seconds must be >= 0")
        if self.max_cycles < 1:
            raise ValueError("max_cycles must be >= 1")
        if self.report_every < 1:
            raise ValueError("report_every must be >= 1")
        if self.convergence_window < 2:
            raise ValueError("convergence_window must be >= 2")
        if self.investigation_window < 2:
            raise ValueError("investigation_window must be >= 2")
        if (
            self.investigate_on_repeat
            and self.investigation_window >= self.convergence_window
        ):
            raise ValueError(
                "investigation_window must be < convergence_window "
                "so investigation fires before the stop-crane"
            )


@dataclass(frozen=True)
class WorkSessionCycleReport:
    """Summary of a single cycle inside a work session."""

    cycle: int
    run_status: str          # "completed" | "stopped" | "blocked"
    tasks_done: int
    tasks_failed: int
    elapsed_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "run_status": self.run_status,
            "tasks_done": self.tasks_done,
            "tasks_failed": self.tasks_failed,
            "elapsed_s": round(self.elapsed_s, 3),
        }

    def user_summary(self) -> str:
        return (
            f"[cycle {self.cycle}] {self.run_status} "
            f"tasks={self.tasks_done} failed={self.tasks_failed} "
            f"elapsed={self.elapsed_s:.2f}s"
        )


@dataclass
class WorkSessionResult:
    """Final result returned by run_work_session()."""

    status: WorkSessionStatus
    goal: str
    dry_run: bool
    cycles_run: int
    stop_reason: str
    cycle_reports: list[WorkSessionCycleReport] = field(default_factory=list)
    total_elapsed_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "goal": self.goal,
            "dry_run": self.dry_run,
            "cycles_run": self.cycles_run,
            "stop_reason": self.stop_reason,
            "total_elapsed_s": round(self.total_elapsed_s, 3),
            "cycles": [cr.to_dict() for cr in self.cycle_reports],
        }

    def user_summary(self) -> str:
        lines = [
            "=== work session ===",
            (
                f"status={self.status}  goal={self.goal!r}  "
                f"dry_run={self.dry_run}"
            ),
            (
                f"cycles={self.cycles_run}  "
                f"elapsed={self.total_elapsed_s:.2f}s"
                + (f"  stop_reason={self.stop_reason}" if self.stop_reason else "")
            ),
        ]
        for cr in self.cycle_reports:
            lines.append(f"  {cr.user_summary()}")
        return "\n".join(lines)


def run_work_session(
    config: WorkSessionConfig,
    *,
    agent: Any,
    workspace: Any,
    approval_inbox: Any = None,
) -> WorkSessionResult:
    """Run a bounded work session (blocking; safe from the CLI and tests).

    Returns a WorkSessionResult when all ``max_cycles`` complete (stop_reason=""), the ``minutes`` budget is exhausted ("time_budget"),
    the circuit breaker trips ("circuit_open: ..."), cycles converge ("converged") or the user presses Ctrl-C (status="interrupted").
    """
    deadline = time.monotonic() + config.minutes * 60.0
    circuit = CircuitBreaker(CircuitBreakerConfig())
    runtime = AutonomousRuntime(
        agent,
        workspace=workspace,
        approval_inbox=approval_inbox,
    )

    cycle_reports: list[WorkSessionCycleReport] = []
    stop_reason: str = ""
    status: WorkSessionStatus = "completed"
    session_start = time.monotonic()
    # Convergence tracking: signature of the previous cycle's outcome and how many times in a row it repeated.
    prev_signature: Any = None
    repeat_count: int = 0
    # Investigation switch locals: the goal the NEXT cycle runs (swapped to the investigation template when the sensor fires, restored after the probe),
    # the one-probe-per-series limiter and the 'probe just ran' marker.
    current_goal: str = config.goal
    investigation_done: bool = False
    investigation_just_ran: bool = False

    _log(agent, "work_session_start", {
        "goal": config.goal,
        "dry_run": config.dry_run,
        "minutes": config.minutes,
        "max_cycles": config.max_cycles,
        "report_every": config.report_every,
    })

    try:
        for cycle in range(1, config.max_cycles + 1):
            # Time budget check before starting a new cycle.
            if time.monotonic() >= deadline:
                stop_reason = "time_budget"
                status = "stopped"
                break

            # Circuit breaker check.
            decision = circuit.check()
            if not decision.allowed:
                stop_reason = f"circuit_open: {decision.reason}"
                status = "stopped"
                break

            _log(agent, "work_session_cycle_start", {
                "cycle": cycle,
                "goal": config.goal,
                "remaining_s": round(max(0.0, deadline - time.monotonic()), 1),
            })

            cycle_start = time.monotonic()

            # include_goal=True for a non-default goal so the agent executes it via agent.run(); queue = [status, learn, goal], so limit=3 keeps the goal task.
            # current_goal, not config.goal: the investigation switch swaps the goal for exactly one cycle.
            has_real_goal = bool(current_goal) and current_goal != "project health"
            run_report = runtime.run(
                AutonomousRuntimeConfig(
                    goal=current_goal,
                    dry_run=config.dry_run,
                    limit=3 if has_real_goal else 2,
                    include_tests=False,  # tests are slow; keep cycles fast
                    include_goal=has_real_goal,
                    # cap goal task at 1 agent.run() call per cycle;
                    # 0 = unlimited (default), used when no real goal.
                    budgets=BudgetLimits(max_agent_runs=1 if has_real_goal else 0),
                )
            )

            cycle_elapsed = time.monotonic() - cycle_start
            tasks_done = sum(
                1 for t in run_report.tasks if t.status == "done"
            )
            tasks_failed = sum(
                1 for t in run_report.tasks if t.status == "failed"
            )
            cr = WorkSessionCycleReport(
                cycle=cycle,
                run_status=run_report.status,
                tasks_done=tasks_done,
                tasks_failed=tasks_failed,
                elapsed_s=cycle_elapsed,
            )
            cycle_reports.append(cr)

            # Approval pause: a cycle blocked on human approval is NOT a failure; park the session with stop_reason "awaiting_approval" WITHOUT recording a circuit-breaker failure.
            # Checked before the budget/convergence/circuit updates so it is never mis-recorded.
            if _is_approval_blocked(run_report):
                _log(agent, "work_session_awaiting_approval", {
                    "cycle": cycle,
                    "goal": config.goal,
                })
                stop_reason = "awaiting_approval"
                status = "stopped"
                break

            # Budget-exhaustion stop: a goal task that died on a model/token budget limit ends the session instead of spinning cycles that would hit the same window;
            # checked before the convergence/circuit update.
            if _is_budget_exhausted(run_report):
                stop_reason = "budget_exhausted"
                status = "stopped"
                _log(agent, "work_session_budget_exhausted", {
                    "cycle": cycle,
                    "goal": config.goal,
                })
                break

            # Convergence stop plus investigation switch: the same outcome signature for `convergence_window` passes means spinning; with investigate_on_repeat a shorter window
            # first fires ONE investigation cycle (next goal = investigation template, then the original goal returns); the stop-crane stays the last resort.
            if config.stop_on_convergence:
                if investigation_just_ran:
                    # Half-open probe: the investigation cycle's report must NOT enter the series counter; skip signature computation, series updates and the stop-crane for it.
                    # The original goal's series resumes next cycle with its previous count. Built as if/else instead of `continue` so cycle-end log, circuit update and pacing still run.
                    current_goal = config.goal
                    investigation_just_ran = False
                else:
                    signature = _cycle_signature(run_report)
                    # Investigation branch: fires BEFORE the stop-crane, on its own (strictly smaller) threshold.
                    if (
                        config.investigate_on_repeat
                        and repeat_count == config.investigation_window
                        and not investigation_done
                    ):
                        investigation_done = True
                        investigation_just_ran = True
                        current_goal = INVESTIGATION_GOAL_TEMPLATE.format(
                            goal=config.goal,
                            signature=signature,
                        )
                        _log(agent, "work_session_investigation", {
                            "cycle": cycle,
                            "repeat_count": repeat_count,
                            "investigation_window": config.investigation_window,
                            "goal": config.goal,
                        })
                        # Do NOT update prev_signature/repeat_count on this pass: the series is preserved and the stop-crane is skipped.
                    else:
                        # Series tracking (non-investigation passes).
                        if signature == prev_signature:
                            repeat_count += 1
                        else:
                            repeat_count = 1
                        prev_signature = signature

                        # Stop-crane.
                        if (
                            repeat_count >= config.convergence_window
                            and cycle < config.max_cycles
                        ):
                            stop_reason = "converged"
                            status = "completed"
                            _log(agent, "work_session_converged", {
                                "cycle": cycle,
                                "repeat_count": repeat_count,
                                "convergence_window": config.convergence_window,
                                "goal": config.goal,
                            })
                            break

            # Update the circuit from the run outcome.
            if run_report.status == "completed":
                circuit.record_success()
            elif run_report.status in ("stopped", "blocked"):
                circuit.record_failure(run_report.stop_reason or run_report.status)

            _log(agent, "work_session_cycle_end", cr.to_dict())

            # Periodic progress report.
            if cycle % config.report_every == 0:
                remaining = max(0.0, deadline - time.monotonic())
                _log(agent, "work_session_report", {
                    "at_cycle": cycle,
                    "cycles_so_far": len(cycle_reports),
                    "elapsed_s": round(time.monotonic() - session_start, 2),
                    "remaining_s": round(remaining, 1),
                })
                print(
                    f"  {cr.user_summary()}  remaining={remaining:.0f}s",
                    file=sys.stderr,
                )

            # Time budget post-cycle check.
            if time.monotonic() >= deadline:
                stop_reason = "time_budget"
                status = "stopped"
                break

            # Pacing pause (marathon heartbeat).
            _pace_pause(agent, config, cycle, cycle_elapsed, deadline)

    except KeyboardInterrupt:
        stop_reason = "interrupted"
        status = "interrupted"

    total_elapsed = time.monotonic() - session_start

    result = WorkSessionResult(
        status=status,
        goal=config.goal,
        dry_run=config.dry_run,
        cycles_run=len(cycle_reports),
        stop_reason=stop_reason,
        cycle_reports=cycle_reports,
        total_elapsed_s=total_elapsed,
    )
    _log(agent, "work_session_stop", result.to_dict())
    return result



def _pace_pause(
    agent: Any,
    config: WorkSessionConfig,
    cycle: int,
    cycle_elapsed: float,
    deadline: float,
) -> None:
    """Sleep out the remainder of this cycle's slot (marathon heartbeat).

    Keeps the shift's burn rate under persistent budget windows. Never after
    the final cycle, never past the deadline: an oversleep would silently eat
    the next cycle's slot and misreport the stop as time_budget work.
    """
    if config.pace_seconds <= 0 or cycle >= config.max_cycles:
        return
    slot_remaining = config.pace_seconds - cycle_elapsed
    pause = min(slot_remaining, deadline - time.monotonic())
    if pause > 0:
        _log(agent, "work_session_pause", {
            "cycle": cycle,
            "sleep_s": round(pause, 1),
            "pace_seconds": config.pace_seconds,
        })
        time.sleep(pause)


def _cycle_signature(run_report: Any) -> tuple:
    """Content signature of one autonomous pass, used for convergence detection.

    Captures *what happened* (overall status plus each task's kind, status and
    summary) while deliberately excluding timing and counts, so two cycles that
    did literally the same work with the same results compare equal. Genuine
    progress changes a task summary and breaks the streak. Best-effort: any
    unexpected shape collapses to a stable fallback so detection never raises.
    """
    try:
        tasks = tuple(
            (
                getattr(t.task, "kind", ""),
                getattr(t, "status", ""),
                getattr(t, "summary", ""),
            )
            for t in getattr(run_report, "tasks", []) or []
        )
        return (getattr(run_report, "status", ""), tasks)
    except Exception as exc:  # noqa: BLE001 — reason stated above
        # The signature feeds repetition detection; `("__unknown__",)` is deliberately CONSTANT so two unreadable reports compare equal and trip the 'nothing is changing' guard (a stuck session stops).
        # Journaled so the operator can tell a loop from an unreadable report.
        logger.warning(
            "run report unreadable, cycle signature degraded: %s: %s",
            type(exc).__name__, exc,
        )
        return ("__unknown__",)


def _is_budget_exhausted(run_report: Any) -> bool:
    """True when a task in the pass failed on a model/token budget limit.

    ModelBudgetExceeded raised inside agent.run() is caught by the runtime and
    surfaces as a failed task whose details carry error_type="ModelBudgetExceeded"
    (see AutonomousRuntime._run_task). Detecting it structurally (not by summary
    text) lets the work session stop early instead of re-running cycles that
    would hit the same exhausted budget window. Best-effort: any unexpected
    shape returns False so detection never raises.
    """
    try:
        for t in getattr(run_report, "tasks", []) or []:
            if getattr(t, "status", "") != "failed":
                continue
            details = getattr(t, "details", None) or {}
            if details.get("error_type") == "ModelBudgetExceeded":
                return True
    except Exception as exc:  # noqa: BLE001 — reason stated above
        # `False` means 'budget is fine, keep going' (permissive): an unreadable report does not stop the session.
        logger.warning(
            "budget-exhaustion check could not read the run report: %s: %s",
            type(exc).__name__, exc,
        )
        return False
    else:
        return False


def _is_approval_blocked(run_report: Any) -> bool:
    """True when the runtime paused waiting on human approval rather than failing.

    An approval pause is the safety design working, not a malfunction, so it must
    not count as a circuit-breaker failure. Detected structurally (not by exact
    message text) so tests can inject a blocked run report robustly:

    * the runtime returns status="blocked" with a stop_reason that mentions
      approval (AutonomousRuntime stamps "approval required: <id>"), or
    * a failed task carries an approval marker in its details.

    Best-effort: any unexpected shape returns False so detection never raises.
    """
    try:
        if getattr(run_report, "status", "") == "blocked":
            reason = getattr(run_report, "stop_reason", "") or ""
            if "approval" in reason.lower():
                return True
        for t in getattr(run_report, "tasks", []) or []:
            if getattr(t, "status", "") != "failed":
                continue
            details = getattr(t, "details", None) or {}
            if details.get("error_type") == "ApprovalRequired":
                return True
            if "approval" in str(details.get("reason", "")).lower():
                return True
    except Exception as exc:  # noqa: BLE001 — reason stated above
        # Same permissive direction as the budget check: an unreadable report removes the reason to pause.
        logger.warning(
            "approval-block check could not read the run report: %s: %s",
            type(exc).__name__, exc,
        )
        return False
    else:
        return False


def _log(agent: Any, event: str, payload: Any) -> None:
    log = getattr(agent, "log", None)
    if log is not None:
        log.log(event, payload)
