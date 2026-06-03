"""Autonomous runtime orchestrator.

This is the first safe autopilot layer. It does not make the agent fully
unsupervised; it gives the system a bounded way to run project-health tasks,
spend from a runtime budget, stop through a circuit breaker, and queue human
approval items instead of silently crossing a risky boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from core.approval_inbox import ApprovalInbox
from core.budget_governor import BudgetGovernor, BudgetLimits, BudgetCounter
from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from core.ingestion import ingest_files
from core.learning_planner import LearningPlanner
from core.models import ToolCall
from core.task_queue import RuntimeTask, TaskQueueStore
from core.reflection import ReflectionConfig, ReflectionEngine


AutonomousTaskKind = Literal["status", "learn", "tests", "goal"]
AutonomousTaskStatus = Literal["pending", "done", "failed", "skipped"]
AutonomousRunStatus = Literal["completed", "stopped", "blocked"]


@dataclass(frozen=True)
class AutonomousRuntimeConfig:
    goal: str = "project health"
    dry_run: bool = True
    effects_approved: bool = False
    limit: int = 5
    include_tests: bool = True
    include_goal: bool = False
    learning_limit: int = 5
    budgets: BudgetLimits = field(default_factory=BudgetLimits)
    circuit: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    # Run ReflectionEngine after the health pass to persist lessons from logs.
    enable_reflection: bool = True
    reflection: ReflectionConfig = field(default_factory=ReflectionConfig)


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

    def user_summary(self) -> str:
        parts = [
            f"(auto-run status={self.status} dry_run={self.dry_run} "
            f"tasks={len(self.tasks)} stop={self.stop_reason or '-'})"
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
    status: str
    processed: list[AutonomousQueuedTaskReport]
    stop_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "processed": [item.to_dict() for item in self.processed],
            "stop_reason": self.stop_reason,
        }

    def user_summary(self) -> str:
        parts = [
            f"(task-run status={self.status}; processed={len(self.processed)}; "
            f"stop={self.stop_reason or '-'})"
        ]
        for item in self.processed:
            parts.append(
                f"  [{item.status}] {item.task_id}: {item.goal} "
                f"run={item.run_status} {item.summary}"
            )
        return "\n".join(parts)


class AutonomousRuntime:
    """Run a bounded autonomous project-health pass."""

    def __init__(
        self,
        agent: Any,
        *,
        workspace: Any,
        approval_inbox: ApprovalInbox | None = None,
    ):
        self.agent = agent
        self.workspace = workspace
        self.approval_inbox = approval_inbox or ApprovalInbox()

    def run_task_queue(
        self,
        task_queue: TaskQueueStore,
        *,
        max_tasks: int = 1,
        task_ids: list[str] | tuple[str, ...] | None = None,
        budgets: BudgetLimits | None = None,
    ) -> AutonomousQueueRunReport:
        if max_tasks < 1:
            raise ValueError("max_tasks must be >= 1")
        if task_ids is None:
            pending = task_queue.pending(limit=max_tasks)
        else:
            pending = task_queue.pending_by_ids(task_ids, limit=max_tasks)
        queue_budget = BudgetGovernor(budgets or BudgetLimits())
        queue_circuit = CircuitBreaker(CircuitBreakerConfig())
        processed: list[AutonomousQueuedTaskReport] = []
        stop_reason = ""
        self._log(
            "autonomous_queue_start",
            {"max_tasks": max_tasks, "pending_selected": [t.id for t in pending]},
        )
        for task in pending:
            task_queue.mark_running(task.id)
            try:
                run_report = self.run(
                    _config_from_task(task),
                    budget=queue_budget,
                    circuit=queue_circuit,
                )
            except Exception as exc:
                failed = task_queue.mark_failed(task.id, error=f"{type(exc).__name__}: {exc}")
                processed.append(
                    AutonomousQueuedTaskReport(
                        task_id=failed.id,
                        goal=failed.goal,
                        status=failed.status,
                        summary=failed.last_error,
                    )
                )
                continue

            if run_report.status == "completed":
                done = task_queue.mark_done(task.id, report=run_report.to_dict())
                processed.append(
                    AutonomousQueuedTaskReport(
                        task_id=done.id,
                        goal=done.goal,
                        status=done.status,
                        run_status=run_report.status,
                        summary=f"tasks={len(run_report.tasks)}",
                    )
                )
                continue

            failed = task_queue.mark_failed(
                task.id,
                error=run_report.stop_reason or run_report.status,
                report=run_report.to_dict(),
            )
            processed.append(
                AutonomousQueuedTaskReport(
                    task_id=failed.id,
                    goal=failed.goal,
                    status=failed.status,
                    run_status=run_report.status,
                    summary=failed.last_error,
                )
            )
            if run_report.status == "stopped":
                stop_reason = run_report.stop_reason or "queue budget or circuit stopped"
                break

        status = "stopped" if stop_reason else ("completed" if processed else "empty")
        report = AutonomousQueueRunReport(status=status, processed=processed, stop_reason=stop_reason)
        self._log("autonomous_queue_stop", report.to_dict())
        return report

    def run(
        self,
        config: AutonomousRuntimeConfig | None = None,
        *,
        budget: BudgetGovernor | None = None,
        circuit: CircuitBreaker | None = None,
    ) -> AutonomousRunReport:
        config = config or AutonomousRuntimeConfig()
        if config.limit < 1:
            raise ValueError("limit must be >= 1")
        if config.learning_limit < 1:
            raise ValueError("learning_limit must be >= 1")

        budget = budget or BudgetGovernor(config.budgets)
        circuit = circuit or CircuitBreaker(config.circuit)
        tasks: list[AutonomousTaskReport] = []

        self._log("autonomous_runtime_start", {
            "goal": config.goal,
            "dry_run": config.dry_run,
            "limit": config.limit,
            "include_tests": config.include_tests,
        })

        if not config.dry_run and not config.effects_approved:
            item = self.approval_inbox.add(
                operation="autonomous_runtime.allow_effects",
                summary=(
                    "Autonomous runtime effects are disabled until a human "
                    "reviews the first dry-run reports and explicitly enables them."
                ),
                risk="irreversible",
                reasons=("non-dry-run autonomous mode is not enabled in this MVP",),
                payload={
                    "goal": config.goal,
                    "dry_run": False,
                    "limit": config.limit,
                    "include_tests": config.include_tests,
                    "learning_limit": config.learning_limit,
                },
            )
            budget.reserve("approval_requests", reason="non-dry-run runtime approval")
            report = AutonomousRunReport(
                status="blocked",
                dry_run=False,
                goal=config.goal,
                tasks=[],
                budget=budget.snapshot(),
                circuit=circuit.snapshot(),
                approvals=self.approval_inbox.snapshot(),
                stop_reason=f"approval required: {item.id}",
            )
            self._log("autonomous_runtime_stop", report.to_dict())
            return report

        queue = self._build_queue(config)[: config.limit]
        for task in queue:
            allowed = circuit.check()
            if not allowed.allowed:
                break
            if not self._reserve(budget, circuit, "cycles", task.kind):
                break
            usage_before = self._usage_counters()
            report = self._run_task(task, config, budget, circuit)
            self._reserve_usage_delta(budget, circuit, usage_before, task.kind)
            tasks.append(report)
            self._log("autonomous_task_result", report.to_dict())
            if report.status == "failed":
                circuit.record_failure(report.summary)
            elif report.status == "done":
                circuit.record_success()

        circuit_decision = circuit.check()
        status: AutonomousRunStatus = "completed" if circuit_decision.allowed else "stopped"
        stop_reason = "" if circuit_decision.allowed else circuit_decision.reason
        report = AutonomousRunReport(
            status=status,
            dry_run=config.dry_run,
            goal=config.goal,
            tasks=tasks,
            budget=budget.snapshot(),
            circuit=circuit.snapshot(),
            approvals=self.approval_inbox.snapshot(),
            stop_reason=stop_reason,
        )

        if config.enable_reflection:
            report.reflection = self._run_reflection(config)

        self._log("autonomous_runtime_stop", report.to_dict())
        return report

    def status(self) -> dict:
        return {
            "approval_inbox": self.approval_inbox.snapshot(),
            "source_registry": self._source_counts(),
            "persistent_memory_records": self._memory_count(),
        }

    def _run_task(
        self,
        task: AutonomousTask,
        config: AutonomousRuntimeConfig,
        budget: BudgetGovernor,
        circuit: CircuitBreaker,
    ) -> AutonomousTaskReport:
        self._log("autonomous_task_start", task.to_dict())
        try:
            if task.kind == "status":
                return self._task_status(task)
            if task.kind == "learn":
                if not self._reserve(budget, circuit, "learning_runs", task.kind):
                    return AutonomousTaskReport(task, "skipped", "learning budget exhausted")
                return self._task_learn(task, config)
            if task.kind == "tests":
                if not self._reserve(budget, circuit, "test_runs", task.kind):
                    return AutonomousTaskReport(task, "skipped", "test budget exhausted")
                return self._task_tests(task)
            if task.kind == "goal":
                if not self._reserve(budget, circuit, "agent_runs", task.kind):
                    return AutonomousTaskReport(task, "skipped", "agent_runs budget exhausted")
                return self._task_goal(task)
        except Exception as exc:
            return AutonomousTaskReport(
                task,
                "failed",
                f"{type(exc).__name__}: {exc}",
            )
        return AutonomousTaskReport(task, "failed", f"unknown task kind: {task.kind}")

    def _task_status(self, task: AutonomousTask) -> AutonomousTaskReport:
        source_counts = self._source_counts()
        memory_count = self._memory_count()
        return AutonomousTaskReport(
            task,
            "done",
            (
                f"sources={source_counts.get('sources', 0)} "
                f"claims={source_counts.get('claims', 0)} memory={memory_count}"
            ),
            {
                "source_registry": source_counts,
                "persistent_memory_records": memory_count,
            },
        )

    def _task_learn(
        self,
        task: AutonomousTask,
        config: AutonomousRuntimeConfig,
    ) -> AutonomousTaskReport:
        plan = LearningPlanner().plan(
            workspace=self.workspace,
            goal=config.goal,
            root=".",
            limit=config.learning_limit,
        )
        self._log("autonomous_learning_plan", plan.to_log_payload())
        if not plan.source_paths:
            return AutonomousTaskReport(task, "skipped", "no learning sources selected")
        ingest = ingest_files(
            agent=self.agent,
            workspace=self.workspace,
            paths=plan.source_paths,
            dry_run=config.dry_run,
            auto_write_memory=False,
        )
        return AutonomousTaskReport(
            task,
            "done",
            f"sources={len(plan.source_paths)} claims={ingest.claim_count} conflicts={ingest.conflicts}",
            {
                "learning_plan": plan.to_log_payload(),
                "ingest": ingest.to_log_payload(),
            },
        )

    def _task_tests(self, task: AutonomousTask) -> AutonomousTaskReport:
        tool = self.agent.registry.get("run_tests")
        call = ToolCall(
            action_id="autonomous_runtime",
            tool_name="run_tests",
            arguments={"paths": ["tests"]},
        )
        result = tool.invoke(call)
        output = result.output if isinstance(result.output, dict) else {}
        if result.status != "success":
            return AutonomousTaskReport(task, "failed", result.error or "run_tests failed")
        failed = int(output.get("failed", 0) or 0) + int(output.get("errors", 0) or 0)
        passed = int(output.get("passed", 0) or 0)
        status: AutonomousTaskStatus = "done" if failed == 0 else "failed"
        return AutonomousTaskReport(
            task,
            status,
            f"passed={passed} failed_or_errors={failed}",
            {
                "exit_code": output.get("exit_code"),
                "timed_out": output.get("timed_out"),
                "passed": passed,
                "failed": output.get("failed", 0),
                "errors": output.get("errors", 0),
                "failed_tests": output.get("failed_tests", []),
            },
        )

    def _reserve(
        self,
        budget: BudgetGovernor,
        circuit: CircuitBreaker,
        counter: BudgetCounter,
        task_kind: str,
    ) -> bool:
        decision = budget.reserve(counter, reason=f"autonomous task: {task_kind}")
        self._log("autonomous_budget", decision.to_dict())
        if decision.allowed:
            return True
        circuit.record_budget_denial(decision.reason)
        return False

    def _reserve_usage_delta(
        self,
        budget: BudgetGovernor,
        circuit: CircuitBreaker,
        before: dict[BudgetCounter, int],
        task_kind: str,
    ) -> bool:
        ok = True
        after = self._usage_counters()
        for counter in ("llm_calls", "web_fetches"):
            delta = max(0, after.get(counter, 0) - before.get(counter, 0))
            if delta < 1:
                continue
            decision = budget.reserve(
                counter,  # type: ignore[arg-type]
                amount=delta,
                reason=f"autonomous task: {task_kind}",
            )
            self._log("autonomous_budget", decision.to_dict())
            if not decision.allowed:
                circuit.record_budget_denial(decision.reason)
                ok = False
        return ok

    def _usage_counters(self) -> dict[BudgetCounter, int]:
        llm = getattr(self.agent, "llm", None)
        call_count = getattr(llm, "call_count", None)
        if call_count is None:
            calls = getattr(llm, "calls", None)
            call_count = len(calls) if isinstance(calls, list) else 0
        chain = getattr(self.agent, "last_provenance", None)
        web_fetches = 0
        by_kind = getattr(chain, "by_kind", None)
        if callable(by_kind):
            try:
                web_fetches = len(by_kind("web_page"))
            except Exception:
                web_fetches = 0
        return {
            "cycles": 0,
            "agent_runs": 0,
            "learning_runs": 0,
            "test_runs": 0,
            "approval_requests": 0,
            "llm_calls": max(0, int(call_count or 0)),
            "web_fetches": max(0, web_fetches),
        }

    def _source_counts(self) -> dict:
        store = getattr(self.agent, "source_registry_store", None)
        if store is None:
            return {"sources": 0, "claims": 0}
        return store.count()

    def _memory_count(self) -> int:
        list_persistent = getattr(self.agent, "list_persistent", None)
        if list_persistent is None:
            return 0
        try:
            return len(list_persistent())
        except Exception:
            return 0

    def _task_goal(self, task: AutonomousTask) -> AutonomousTaskReport:
        """Run task.description as a user question through the parent AgentLoop."""
        answer = self.agent.run(user_question=task.description)
        summary = (answer[:120].replace("\n", " ")) if answer else "(no answer)"
        return AutonomousTaskReport(
            task,
            "done",
            summary,
            {"answer": answer},
        )

    def _build_queue(self, config: AutonomousRuntimeConfig) -> list[AutonomousTask]:
        tasks = [
            AutonomousTask("status", "Inspect current source registry and memory state."),
            AutonomousTask("learn", "Plan and dry-run ingest high-value project sources."),
        ]
        if config.include_tests:
            tasks.append(AutonomousTask("tests", "Run the pytest suite as a health check."))
        if config.include_goal and config.goal and config.goal != "project health":
            tasks.append(AutonomousTask("goal", config.goal))
        return tasks

    def _run_reflection(self, config: AutonomousRuntimeConfig) -> dict | None:
        """Run ReflectionEngine after a health pass. Returns dict or None on error."""
        persistent_store = getattr(self.agent, "persistent_store", None)
        llm = getattr(self.agent, "llm", None)
        if persistent_store is None or llm is None:
            self._log("reflection_skipped", {
                "reason": "agent missing persistent_store or llm"
            })
            return None
        log = getattr(self.agent, "log", None)
        log_dir = getattr(log, "log_dir", None) if log is not None else None
        engine = ReflectionEngine(
            workspace=self.workspace,
            persistent_memory=persistent_store,
            llm=llm,
            log_dir=log_dir,
            logger=log,
        )
        try:
            result = engine.reflect(config.reflection)
            # ── Consume the LearningPlan: ingest the files the reflection engine
            # identified as weak spots so the agent actually studies them.
            if result.learning_plan and result.learning_plan.source_paths:
                try:
                    ingest = ingest_files(
                        agent=self.agent,
                        workspace=self.workspace,
                        paths=result.learning_plan.source_paths,
                        dry_run=config.dry_run,
                        auto_write_memory=False,
                    )
                    self._log(
                        "reflection_learning_ingest",
                        {
                            "sources": len(result.learning_plan.source_paths),
                            "claims": ingest.claim_count,
                            "conflicts": ingest.conflicts,
                            "dry_run": config.dry_run,
                        },
                    )
                except Exception as ingest_exc:
                    self._log(
                        "reflection_learning_ingest_error",
                        {"error": f"{type(ingest_exc).__name__}: {ingest_exc}"},
                    )
            return result.to_dict()
        except Exception as exc:
            self._log("reflection_error", {"error": f"{type(exc).__name__}: {exc}"})
            return {"error": str(exc)}

    def _log(self, event: str, payload: Any) -> None:
        log = getattr(self.agent, "log", None)
        if log is not None:
            log.log(event, payload)


def _config_from_task(task: RuntimeTask) -> AutonomousRuntimeConfig:
    if task.kind != "auto_run":
        raise ValueError(f"unsupported runtime task kind: {task.kind}")
    return AutonomousRuntimeConfig(
        goal=task.goal,
        dry_run=task.dry_run,
        limit=task.limit,
        include_tests=task.include_tests,
        learning_limit=task.learning_limit,
    )
