"""MVP-17.1  Long Work Session Skeleton.

A bounded multi-cycle session that:

* runs up to ``max_cycles`` autonomous health passes
* stops when the wall-clock ``minutes`` budget expires
* stops when the circuit breaker opens (too many consecutive failures)
* stops early when successive cycles converge — i.e. produce an identical
  outcome signature ``convergence_window`` times in a row (TD-018), so a
  session that keeps re-running the same goal with the same result does not
  burn its whole budget spinning in place
* emits a progress line every ``report_every`` cycles to stderr
* never performs side-effects in dry-run mode (delegates to AutonomousRuntime)

This is the first layer only — a single-command skeleton that proves the
timing/budget/circuit mechanics work before real multi-hour sessions land.

Usage (CLI):
    :work-session "project health" --dry-run --minutes 10 --max-cycles 3 --report-every 1
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig
from core.budget_governor import BudgetLimits
from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig


WorkSessionStatus = Literal["completed", "stopped", "interrupted"]
WorkSessionStopReason = Literal[
    "max_cycles", "time_budget", "circuit_open", "converged", "budget_exhausted",
    "awaiting_approval", "interrupted", ""
]


@dataclass(frozen=True)
class WorkSessionConfig:
    """Immutable configuration for one work session."""

    goal: str = "project health"
    # dry_run=False means real effects; the AutonomousRuntime will still block
    # unless effects_approved=True, so this is safe to default to False.
    # Pass dry_run=True explicitly from the CLI when you want a read-only pass.
    dry_run: bool = False
    minutes: float = 10.0
    max_cycles: int = 3
    report_every: int = 1
    # TD-018 convergence stop: when successive cycles produce an identical
    # outcome signature this many times in a row *and* cycles remain, the
    # session stops early (stop_reason="converged") instead of spinning on a
    # goal it can no longer make progress on. Set False to disable.
    stop_on_convergence: bool = True
    convergence_window: int = 3

    def __post_init__(self) -> None:
        if self.minutes <= 0:
            raise ValueError("minutes must be > 0")
        if self.max_cycles < 1:
            raise ValueError("max_cycles must be >= 1")
        if self.report_every < 1:
            raise ValueError("report_every must be >= 1")
        if self.convergence_window < 2:
            raise ValueError("convergence_window must be >= 2")


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
    """Run a bounded work session.  Safe to call from the CLI and tests.

    The function is blocking.  It returns a WorkSessionResult when:
    - all ``max_cycles`` complete  (stop_reason="")
    - wall-clock ``minutes`` are exhausted  (stop_reason="time_budget")
    - the circuit breaker trips   (stop_reason="circuit_open: ...")
    - successive cycles converge  (stop_reason="converged", TD-018)
    - the user presses Ctrl-C     (status="interrupted")
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
    # TD-018 convergence tracking: signature of the previous cycle's outcome and
    # how many times in a row the same signature has repeated.
    prev_signature: Any = None
    repeat_count: int = 0

    _log(agent, "work_session_start", {
        "goal": config.goal,
        "dry_run": config.dry_run,
        "minutes": config.minutes,
        "max_cycles": config.max_cycles,
        "report_every": config.report_every,
    })

    try:
        for cycle in range(1, config.max_cycles + 1):
            # ── time budget check before starting a new cycle ───────────────
            if time.monotonic() >= deadline:
                stop_reason = "time_budget"
                status = "stopped"
                break

            # ── circuit breaker check ────────────────────────────────────────
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

            # ── one autonomous pass per cycle ─────────────────────────────────
            # include_goal=True when a non-default goal was supplied so the
            # agent actually executes it via agent.run().  Queue = [status,
            # learn, goal] → need limit=3 so the goal task is not cut off.
            has_real_goal = bool(config.goal) and config.goal != "project health"
            run_report = runtime.run(
                AutonomousRuntimeConfig(
                    goal=config.goal,
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

            # ── approval-pause stop ──────────────────────────────────────────
            # A cycle that blocked waiting for human approval is NOT a failure —
            # it is the safety design working. Park the session with an explicit
            # "awaiting_approval" stop_reason and event, WITHOUT recording a
            # circuit-breaker failure. Otherwise two approval-blocked cycles in a
            # row would exhaust the failure budget and open the breaker, hiding
            # the real reason behind a scary "circuit_open" message. Checked
            # before the budget/convergence/circuit updates so an approval pause
            # is never mis-recorded as a failure.
            if _is_approval_blocked(run_report):
                _log(agent, "work_session_awaiting_approval", {
                    "cycle": cycle,
                    "goal": config.goal,
                })
                stop_reason = "awaiting_approval"
                status = "stopped"
                break

            # ── budget-exhaustion stop ───────────────────────────────────────
            # If the cycle's goal task died on a model/token budget limit
            # (ModelBudgetExceeded surfaces as a failed task carrying
            # error_type="ModelBudgetExceeded"), stop now instead of spinning
            # the remaining cycles, which would each hit the same exhausted
            # window. Checked before the convergence/circuit update so a
            # starved cycle is never mis-recorded as a success.
            if _is_budget_exhausted(run_report):
                stop_reason = "budget_exhausted"
                status = "stopped"
                _log(agent, "work_session_budget_exhausted", {
                    "cycle": cycle,
                    "goal": config.goal,
                })
                break

            # ── convergence stop (TD-018) ────────────────────────────────────
            # If the cycle produced the same outcome signature as the previous
            # ones for `convergence_window` passes in a row, the session is
            # spinning (re-running the same goal with the same result). Stop
            # early — but only while cycles remain, so a run that naturally ends
            # on its last cycle still reports stop_reason="" as before.
            if config.stop_on_convergence:
                signature = _cycle_signature(run_report)
                if signature == prev_signature:
                    repeat_count += 1
                else:
                    repeat_count = 1
                prev_signature = signature
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

            # ── update circuit based on run outcome ──────────────────────────
            if run_report.status == "completed":
                circuit.record_success()
            elif run_report.status in ("stopped", "blocked"):
                circuit.record_failure(run_report.stop_reason or run_report.status)

            _log(agent, "work_session_cycle_end", cr.to_dict())

            # ── periodic progress report ─────────────────────────────────────
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

            # ── time budget post-cycle check ─────────────────────────────────
            if time.monotonic() >= deadline:
                stop_reason = "time_budget"
                status = "stopped"
                break

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


# ── internal helpers ───────────────────────────────────────────────────────────

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
    except Exception:
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
        return False
    except Exception:
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
        return False
    except Exception:
        return False


def _log(agent: Any, event: str, payload: Any) -> None:
    log = getattr(agent, "log", None)
    if log is not None:
        log.log(event, payload)
