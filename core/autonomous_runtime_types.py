"""Data carried between the autonomous runtime and its callers; `core/autonomous_runtime` re-exports every name."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from core.budget_governor import BudgetLimits
from core.circuit_breaker import CircuitBreakerConfig
from core.reflection import ReflectionConfig

#: Statuses and kinds these carriers speak in.
AutonomousRunStatus = Literal["completed", "stopped", "blocked"]
AutonomousTaskKind = Literal["status", "learn", "tests", "goal", "propose"]
AutonomousTaskStatus = Literal["pending", "done", "failed", "skipped", "inconclusive", "clarify"]


#: `kw_only`: field order of a growing config is not a contract; positional misuse must fail loudly.
@dataclass(frozen=True, kw_only=True)
class AutonomousRuntimeConfig:
    goal: str = "project health"
    #: Success criterion of THIS goal, verbatim as the charter chose it (see core/success_check.py).
    goal_success_check: str = ""
    dry_run: bool = True
    effects_approved: bool = False
    #: Narrow unblocking of the goal path (intersects _UNBLOCKABLE_TOOLS).
    unblock_tools: frozenset[str] = frozenset()
    limit: int = 5
    include_tests: bool = True
    include_goal: bool = False
    learning_limit: int = 5
    #: May an unattended run write what it learned into long-term memory? OFF by default; both learning sites also pass `require_verified=True` (a second source must corroborate).
    learning_writes_memory: bool = False
    budgets: BudgetLimits = field(default_factory=BudgetLimits)
    circuit: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    # run ReflectionEngine after the health pass to persist lessons from logs
    enable_reflection: bool = True
    reflection: ReflectionConfig = field(default_factory=ReflectionConfig)
    # after the health pass, PROPOSE (never apply) at most one low-risk self-build split into the approval inbox; skipped in dry runs
    enable_self_build: bool = True
    # append a 'propose' task asking the LLM for 1-3 bounded task ideas, written to the approval inbox without executing; opt-in
    include_proposals: bool = False


@dataclass(frozen=True)
class AutonomousTask:
    kind: AutonomousTaskKind
    description: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "description": self.description}


@dataclass
class AutonomousTaskReport:
    task: AutonomousTask
    status: AutonomousTaskStatus
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "task": self.task.to_dict(),
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
        }


@dataclass
class AutonomousRunReport:
    status: AutonomousRunStatus
    dry_run: bool
    goal: str
    tasks: list[AutonomousTaskReport]
    budget: dict
    circuit: dict
    approvals: dict
    stop_reason: str = ""
    # Populated when AutonomousRuntimeConfig.enable_reflection=True.
    reflection: dict | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "dry_run": self.dry_run,
            "goal": self.goal,
            "tasks": [task.to_dict() for task in self.tasks],
            "budget": self.budget,
            "circuit": self.circuit,
            "approvals": self.approvals,
            "stop_reason": self.stop_reason,
            "reflection": self.reflection,
        }

    def for_log(self) -> dict:
        """`to_dict` for the log: the approval inbox as an inventory, without request bodies (keeps the trace small)."""
        out = self.to_dict()
        approvals = dict(out.get("approvals") or {})
        approvals["items"] = [
            {k: item.get(k) for k in ("id", "operation", "status", "summary", "risk", "created_at")}
            for item in approvals.get("items") or [] if isinstance(item, dict)
        ]
        out["approvals"] = approvals
        return out

    def attempted(self) -> bool:
        """Whether an ATTEMPT happened: something started or something was spent.

        Burn key of the one-shot approval: an attempt burns it, a refusal before start (skipped/failed at zero spend) does not.
        """
        if any(t.status in ("done", "clarify", "inconclusive") for t in self.tasks):
            return True
        used = self.budget.get("used", {}) if isinstance(self.budget, dict) else {}
        return int(used.get("llm_calls") or 0) > 0 or int(used.get("web_fetches") or 0) > 0

    #: Tasks whose "done" is not work: the state probe and the learning pass.
    _NOT_WORK_KINDS = frozenset({"status", "learn"})

    def semantic_result(self) -> tuple[str, bool]:
        """(result, work_done): the outcome, not the lifecycle.

        work_done = at least one WORK task (goal, proposal, tests) is ``done``, not the state probe.
        """
        work = any(
            t.status == "done"
            and getattr(getattr(t, "task", None), "kind", "") not in self._NOT_WORK_KINDS
            for t in self.tasks
        )
        if self.status in ("stopped", "blocked"):
            return self.status, work
        if work:
            return "completed", True
        if any(t.status == "failed" for t in self.tasks):
            return "failed", False
        return "empty", False

    def user_summary(self) -> str:
        parts = [
            (f"(auto-run status={self.status} dry_run={self.dry_run} "
            f"tasks={len(self.tasks)} stop={self.stop_reason or '-'})")
        ]
        for report in self.tasks:
            parts.append(
                f"  [{report.status}] {report.task.kind}: {report.summary}"
            )
        pending = self.approvals.get("pending", 0)
        if pending:
            parts.append(f"  approvals_pending={pending}")
        denials = self.budget.get("denials", [])
        if denials:
            parts.append(f"  budget_denials={len(denials)}")
        if self.reflection:
            saved = self.reflection.get("memory_records_saved", 0)
            lessons = self.reflection.get("lessons_count", 0)
            parts.append(f"  reflection: lessons={lessons} saved={saved}")
        return "\n".join(parts)


@dataclass
class AutonomousQueuedTaskReport:
    task_id: str
    goal: str
    status: str
    run_status: str = ""
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status,
            "run_status": self.run_status,
            "summary": self.summary,
        }


@dataclass
class AutonomousQueueRunReport:
    """Two facts, deliberately not merged.

    ``status`` is the QUEUE lifecycle (``completed`` = tasks were processed, a failed task also satisfies it; kept as-is, read across the codebase).
    The ``work_*``/``*_count`` members say whether any of it actually worked, derived from the per-task statuses.
    """

    status: str
    processed: list[AutonomousQueuedTaskReport]
    stop_reason: str = ""

    #: Only `done` is work performed; `blocked` rests awaiting a human.
    _SUCCESS_STATUSES = frozenset({"done"})

    @property
    def succeeded_count(self) -> int:
        return sum(1 for t in self.processed if t.status in self._SUCCESS_STATUSES)

    @property
    def failed_count(self) -> int:
        return sum(1 for t in self.processed if t.status == "failed")

    @property
    def work_succeeded(self) -> bool:
        """At least one processed task actually finished its work."""
        return self.succeeded_count > 0

    @property
    def work_partial(self) -> bool:
        """Achieved something AND failed something — what one verdict cannot say."""
        return self.succeeded_count > 0 and self.failed_count > 0

    def semantic_result(self) -> tuple[str, bool]:
        """(result, work_done) for consumers that mean OUTCOME, not lifecycle.

        A hollow ``completed`` becomes ``failed``; a stopped run that achieved something still admits the work happened.
        """
        if self.status == "stopped":
            return "stopped", self.work_succeeded
        if self.work_succeeded:
            return "completed", True
        if self.failed_count > 0:
            return "failed", False
        return self.status, False

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "processed": [item.to_dict() for item in self.processed],
            "stop_reason": self.stop_reason,
            # Serialised: a distinction that never leaves the process is unauditable.
            "work_succeeded": self.work_succeeded,
            "work_partial": self.work_partial,
            "succeeded_count": self.succeeded_count,
            "failed_count": self.failed_count,
        }

    def user_summary(self) -> str:
        parts = [
            # `processed` alone reads as achievement; the counts prevent that.
            (f"(task-run status={self.status}; processed={len(self.processed)}; "
            f"succeeded={self.succeeded_count}; failed={self.failed_count}; "
            f"stop={self.stop_reason or '-'})")
        ]
        for item in self.processed:
            parts.append(
                f"  [{item.status}] {item.task_id}: {item.goal} "
                f"run={item.run_status} {item.summary}"
            )
        return "\n".join(parts)
