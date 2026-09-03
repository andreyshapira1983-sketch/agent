"""Data carried between the autonomous runtime and its callers.

Cut out of `core/autonomous_runtime.py` on 2026-08-22, on the operator's
reasoning: a file too large to read through produces mistakes — his, mine, and
the agent's own when it reads its own source. The orchestrator class alone was
1251 lines of a 1668-line file whose recorded aspiration is 1150; these six
carriers sat beside it for no reason but history. `core/campaign_types.py` is
the pattern this follows.

Moved verbatim by AST line-span, never retyped, so no body could drift during
the move. `core/autonomous_runtime` re-exports every name, so existing imports
keep working — this is a relocation, not an interface change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from core.budget_governor import BudgetLimits
from core.circuit_breaker import CircuitBreakerConfig
from core.reflection import ReflectionConfig

#: Statuses and kinds these carriers speak in. Moved with them: a report whose
#: vocabulary lives in another module is half a definition.
AutonomousRunStatus = Literal["completed", "stopped", "blocked"]
AutonomousTaskKind = Literal["status", "learn", "tests", "goal", "propose"]
AutonomousTaskStatus = Literal["pending", "done", "failed", "skipped", "inconclusive", "clarify"]


@dataclass(frozen=True)
class AutonomousRuntimeConfig:
    goal: str = "project health"
    dry_run: bool = True
    effects_approved: bool = False
    #: Узкая разблокировка целевого пути (пересекается с _UNBLOCKABLE_TOOLS).
    unblock_tools: frozenset[str] = frozenset()
    limit: int = 5
    include_tests: bool = True
    include_goal: bool = False
    learning_limit: int = 5
    #: May an unattended run write what it learned into long-term memory?
    #: OFF by default; until 2026-08-05 `auto_write_memory=False` was
    #: hardcoded at both learning call sites and the operator could not say
    #: yes at all. Turning it on does not open the door: both sites also
    #: pass `require_verified=True`, so a claim enters memory only when a
    #: SECOND source corroborated it. Measured — a single `.md` or web page
    #: is otherwise saved at confidence 0.90 and nobody is watching an
    #: overnight run. (Code sources were already refused by
    #: `KnowledgeWritePolicy`: programs do not assert facts.)
    learning_writes_memory: bool = False
    budgets: BudgetLimits = field(default_factory=BudgetLimits)
    circuit: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    # Run ReflectionEngine after the health pass to persist lessons from logs.
    enable_reflection: bool = True
    reflection: ReflectionConfig = field(default_factory=ReflectionConfig)
    # After the health pass, autonomously PROPOSE (never apply) at most one
    # low-risk self-build split into the approval inbox. Applying stays behind
    # the human :self-apply-run gate, so this only ever creates a pending item
    # for review. Skipped during dry runs (no real approval artifacts).
    enable_self_build: bool = True
    # When True, append a 'propose' task that asks the LLM for 1-3 bounded
    # task ideas and writes them to the approval inbox WITHOUT executing
    # any of them. Off by default — proposals are an opt-in autonomy feature.
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

    def attempted(self) -> bool:
        """Была ли ПОПЫТКА: что-то началось или что-то потрачено.

        Ключ сжигания одноразового «да» (MIR-117, слово оператора 2026-08-27):
        одобрение сгорает попыткой, но отказ до старта — задачи skipped/failed
        при нуле трат — попыткой не является, и грант остаётся жить.
        """
        if any(t.status in ("done", "clarify", "inconclusive") for t in self.tasks):
            return True
        used = self.budget.get("used", {}) if isinstance(self.budget, dict) else {}
        return int(used.get("llm_calls") or 0) > 0 or int(used.get("web_fetches") or 0) > 0

    #: Задачи, чьё «done» — не работа: служебная проба состояния и учебный
    #: проход. Аудит 2026-09-03 (A1): очередь КАЖДОГО прогона начинается с
    #: `status`, она всегда done, и любой прогон — даже с одним встречным
    #: вопросом — читался как «работа была».
    _NOT_WORK_KINDS = frozenset({"status", "learn"})

    def semantic_result(self) -> tuple[str, bool]:
        """(result, work_done) — исход, а не жизненный цикл (MIR-117, норма A).

        ``status='completed'`` чеканится по «дочерпали», и прогон, чью
        единственную задачу отверг ценовой конверт, носил его как достижение.
        Здесь слово следует за работой: сделана = хотя бы одна РАБОЧАЯ задача
        ``done`` (цель, предложение, тесты) — не проба состояния.
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
    """Two facts, deliberately not merged (MIR-117; norm A, ratified 2026-08-22).

    ``status`` is the QUEUE lifecycle: ``completed`` means tasks were PROCESSED,
    which a failed task also satisfies. Kept as-is — the field is read across
    the codebase. The ``work_*``/``*_count`` members answer what nothing could
    ask before: did any of it actually work, derived from the per-task statuses
    already held. Forcing case: a campaign whose only task was refused on budget
    still recorded ``completed`` and counted a useful cycle.
    """

    status: str
    processed: list[AutonomousQueuedTaskReport]
    stop_reason: str = ""

    #: Only `done` is work performed; `blocked` rests awaiting a human (MIR-039).
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

        MIR-117: the campaign cycle copied ``status`` verbatim, so a run whose
        only task was refused pre-flight recorded ``completed``. Here the word
        follows the work: hollow ``completed`` becomes ``failed``; a stopped
        run that achieved something still admits the work happened.
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
