"""Autonomous runtime orchestrator.

This is the first safe autopilot layer. It does not make the agent fully
unsupervised; it gives the system a bounded way to run project-health tasks,
spend from a runtime budget, stop through a circuit breaker, and queue human
approval items instead of silently crossing a risky boundary.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
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


AutonomousTaskKind = Literal["status", "learn", "tests", "goal", "propose"]
AutonomousTaskStatus = Literal["pending", "done", "failed", "skipped"]
AutonomousRunStatus = Literal["completed", "stopped", "blocked"]

# Rotated per 10-min bucket so successive auto-run ticks study different
# subtrees and reflect over different log windows instead of repeating the
# same input. Stateless: derived from wall clock.
_LEARN_ROOT_ROTATION: tuple[str, ...] = (".", "core", "tools", "tests", "scripts")
_REFLECTION_LOG_WINDOWS: tuple[int, ...] = (20, 40, 30, 60)


def _rotation_index(modulus: int, *, bucket_seconds: int = 600) -> int:
    return int(time.time() // bucket_seconds) % max(modulus, 1)


# --- Proposal hygiene: canonical signature + token-Jaccard semantic dedup ---
#
# sha256(description) only catches verbatim repeats. Sonnet rewords the same
# idea every tick, so the inbox accumulates near-duplicates. Below we extract
# a canonical token set per proposal and reject new proposals whose tokens
# overlap an existing same-kind proposal at >= _PROPOSAL_JACCARD_THRESHOLD.
# Kind is part of the signature: a "learn" claim-distribution and a "goal"
# claim-distribution stay separate (they imply different work).
_PROPOSAL_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "by", "with",
    "from", "is", "are", "be", "that", "this", "these", "those", "it", "its",
    "as", "at", "across", "into", "their", "there", "which", "what", "than",
    "then", "also", "we", "our", "i", "you", "your", "they", "them", "not",
    "no", "but", "so", "if", "via", "about", "any", "all", "some", "more",
    "most", "less", "few", "each", "other", "such", "may", "can", "will",
    "would", "should", "could", "have", "has", "had", "been", "being",
    "ensure", "help", "help", "use", "uses", "used",
})
_PROPOSAL_JACCARD_THRESHOLD: float = 0.4


def _proposal_stem(token: str) -> str:
    """Strip a few common English suffixes so 'claim'/'claims' collapse.

    Intentionally tiny and deterministic — no NLTK, no language detection.
    Good enough to make Sonnet's reworded near-duplicates collide on the
    Jaccard threshold below.
    """
    for suffix in ("ization", "ations", "ation", "ings", "ies", "ied", "ing", "ers", "ers", "ed", "es", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            base = token[: -len(suffix)]
            if suffix == "ies":
                base += "y"
            return base
    return token


def _proposal_tokens(text: str) -> frozenset[str]:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9_]+", " ", text)
    return frozenset(
        _proposal_stem(t)
        for t in text.split()
        if len(t) > 2 and t not in _PROPOSAL_STOPWORDS
    )


def _proposal_canonical_signature(kind: str, description: str) -> str:
    """Deterministic, human-readable bucket key like 'tests:claim:registry:source'.

    Different `kind` always yields different signatures. Within a kind, two
    descriptions collapse to the same signature only if their token-sets are
    identical *after* taking the top alphabetic slice; this is intentionally
    coarse so the operator can grep the inbox.
    """
    kind = (kind or "other").strip().lower() or "other"
    toks = sorted(_proposal_tokens(description))
    return f"{kind}:{':'.join(toks[:6])}"


def _proposal_jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)



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
            if task.kind == "propose":
                return self._task_propose(task, config, budget)
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
        source_registry = getattr(self.agent, "source_registry_store", None)
        rotated_root = _LEARN_ROOT_ROTATION[_rotation_index(len(_LEARN_ROOT_ROTATION))]
        if rotated_root != "." and not (self.workspace / rotated_root).is_dir():
            rotated_root = "."
        plan = LearningPlanner().plan(
            workspace=self.workspace,
            goal=config.goal,
            root=rotated_root,
            limit=config.learning_limit,
            source_registry=source_registry,
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

    def _task_propose(
        self,
        task: AutonomousTask,
        config: AutonomousRuntimeConfig,
        budget: BudgetGovernor,
    ) -> AutonomousTaskReport:
        """Ask the LLM for 1-3 bounded next-task proposals and write them to
        the approval inbox. Never executes any proposal."""
        cap = max(0, int(budget.limits.max_proposals_per_run))
        if cap == 0:
            return AutonomousTaskReport(task, "skipped", "proposals disabled (cap=0)")

        llm = getattr(self.agent, "llm", None)
        if llm is None:
            return AutonomousTaskReport(task, "skipped", "agent has no llm")

        digest = self._proposal_digest()
        existing_hashes, existing_token_sets = self._existing_proposal_fingerprints()

        system = (
            "You propose next tasks for a bounded autonomous coding agent. "
            "Each proposal must be small, reversible, and reviewable by a human. "
            "Reply with STRICT JSON only, matching this schema: "
            "{\"proposals\":[{\"kind\":\"learn|tests|goal|other\","
            "\"description\":\"<<= 160 chars>\",\"rationale\":\"<<= 280 chars>\","
            "\"est_cost\":\"low|medium|high\"}]}. "
            "Return between 1 and 3 proposals. No prose, no markdown."
        )
        user = (
            "Current state digest:\n" + json.dumps(digest, ensure_ascii=False, indent=2)
            + "\n\nReturn JSON with 1-3 distinct proposals."
        )

        try:
            raw = llm.complete(system=system, user=user, max_tokens=800, temperature=0.4)
        except Exception as exc:
            self._log("propose_llm_failed", {"error": f"{type(exc).__name__}: {exc}"})
            return AutonomousTaskReport(task, "failed", "llm error", {"error": str(exc)})

        proposals = self._parse_proposals(raw)
        if proposals is None:
            self._log("propose_parse_failed", {"raw": (raw or "")[:400]})
            return AutonomousTaskReport(task, "failed", "proposals JSON malformed", {"raw_head": (raw or "")[:200]})

        written: list[dict] = []
        skipped_dupes = 0
        skipped_semantic = 0
        for proposal in proposals:
            if len(written) >= cap:
                break
            description = (proposal.get("description") or "").strip()
            rationale = (proposal.get("rationale") or "").strip()
            kind = (proposal.get("kind") or "other").strip().lower()
            est_cost = (proposal.get("est_cost") or "low").strip().lower()
            if not description:
                continue
            if kind not in {"learn", "tests", "goal", "other"}:
                kind = "other"
            if est_cost not in {"low", "medium", "high"}:
                est_cost = "low"
            description = description[:160]
            rationale = rationale[:280]
            phash = hashlib.sha256(description.lower().encode("utf-8")).hexdigest()[:16]
            if phash in existing_hashes:
                skipped_dupes += 1
                continue
            tokens = _proposal_tokens(description)
            best_overlap = 0.0
            for prev_kind, prev_tokens in existing_token_sets:
                if prev_kind != kind:
                    continue
                overlap = _proposal_jaccard(tokens, prev_tokens)
                if overlap > best_overlap:
                    best_overlap = overlap
            if best_overlap >= _PROPOSAL_JACCARD_THRESHOLD:
                skipped_semantic += 1
                self._log(
                    "proposal_semantic_dupe",
                    {
                        "kind": kind,
                        "jaccard": round(best_overlap, 3),
                        "threshold": _PROPOSAL_JACCARD_THRESHOLD,
                        "description": description,
                    },
                )
                continue
            signature = _proposal_canonical_signature(kind, description)
            existing_hashes.add(phash)
            existing_token_sets.append((kind, tokens))
            item = self.approval_inbox.add(
                operation="proposed_task",
                summary=description,
                risk="reversible",
                reasons=(rationale,) if rationale else (),
                payload={
                    "kind": kind,
                    "est_cost": est_cost,
                    "description": description,
                    "rationale": rationale,
                    "hash": phash,
                    "canonical_signature": signature,
                },
            )
            written.append(
                {
                    "id": item.id,
                    "kind": kind,
                    "description": description,
                    "canonical_signature": signature,
                }
            )

        cluster_summary: dict[str, int] = {}
        for entry in written:
            sig = entry.get("canonical_signature") or ""
            cluster_summary[sig] = cluster_summary.get(sig, 0) + 1
        if cluster_summary:
            self._log(
                "proposal_cluster",
                {"written": len(written), "clusters": cluster_summary},
            )

        summary = (
            f"proposals_written={len(written)} "
            f"dupes={skipped_dupes} semantic_dupes={skipped_semantic}"
        )
        return AutonomousTaskReport(
            task,
            "done",
            summary,
            {
                "written": written,
                "skipped_dupes": skipped_dupes,
                "skipped_semantic_dupes": skipped_semantic,
                "raw_count": len(proposals),
            },
        )

    def _proposal_digest(self) -> dict:
        snap = self.approval_inbox.snapshot()
        digest: dict[str, Any] = {
            "source_registry": self._source_counts(),
            "persistent_memory_records": self._memory_count(),
            "approval_inbox_pending": int(snap.get("pending") or 0),
        }
        # Recent lessons from persistent memory (tagged "lesson"), best-effort.
        store = getattr(self.agent, "persistent_store", None)
        if store is not None:
            try:
                records = store.load()
                lessons = [
                    (getattr(r, "content", None) or getattr(r, "text", None) or str(r))[:240]
                    for r in records
                    if "lesson" in (getattr(r, "tags", ()) or ())
                ]
                digest["recent_lessons"] = lessons[-5:]
            except Exception:
                digest["recent_lessons"] = []
        return digest

    def _existing_proposal_fingerprints(
        self,
    ) -> tuple[set[str], list[tuple[str, frozenset[str]]]]:
        """Return (sha256_hashes, [(kind, token_set), ...]) for pending proposals.

        Hashes catch verbatim repeats. Token sets feed the Jaccard-based
        semantic dedup so reworded near-duplicates do not bloat the inbox.
        """
        hashes: set[str] = set()
        token_sets: list[tuple[str, frozenset[str]]] = []
        try:
            snap = self.approval_inbox.snapshot()
        except Exception:
            return hashes, token_sets
        for item in snap.get("items", []) or []:
            if item.get("operation") != "proposed_task":
                continue
            if item.get("status") != "pending":
                continue
            payload = item.get("payload") or {}
            desc = payload.get("description") or item.get("summary") or ""
            kind = (payload.get("kind") or "other").strip().lower() or "other"
            phash = payload.get("hash")
            if isinstance(phash, str) and phash:
                hashes.add(phash)
            elif desc:
                hashes.add(hashlib.sha256(desc.lower().encode("utf-8")).hexdigest()[:16])
            if desc:
                token_sets.append((kind, _proposal_tokens(desc)))
        return hashes, token_sets

    @staticmethod
    def _parse_proposals(raw: str | None) -> list[dict] | None:
        if not raw:
            return None
        text = raw.strip()
        # Strip ```json fences if present.
        if text.startswith("```"):
            text = text.strip("`")
            # remove a leading "json" language tag
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        # Find first JSON object.
        try:
            data = json.loads(text)
        except Exception:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                return None
            try:
                data = json.loads(text[start : end + 1])
            except Exception:
                return None
        if not isinstance(data, dict):
            return None
        proposals = data.get("proposals")
        if not isinstance(proposals, list):
            return None
        out: list[dict] = []
        for entry in proposals:
            if isinstance(entry, dict):
                out.append(entry)
        return out

    def _build_queue(self, config: AutonomousRuntimeConfig) -> list[AutonomousTask]:
        tasks = [
            AutonomousTask("status", "Inspect current source registry and memory state."),
            AutonomousTask("learn", "Plan and dry-run ingest high-value project sources."),
        ]
        if config.include_tests:
            tasks.append(AutonomousTask("tests", "Run the pytest suite as a health check."))
        if config.include_goal and config.goal and config.goal != "project health":
            tasks.append(AutonomousTask("goal", config.goal))
        if config.include_proposals:
            tasks.append(AutonomousTask("propose", "Propose 1-3 bounded next tasks for human review."))
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
        rotated_max_logs = _REFLECTION_LOG_WINDOWS[_rotation_index(len(_REFLECTION_LOG_WINDOWS))]
        rotated_reflection = ReflectionConfig(
            max_logs=rotated_max_logs,
            min_occurrences=config.reflection.min_occurrences,
            max_lessons=config.reflection.max_lessons,
            learning_limit=config.reflection.learning_limit,
        )
        try:
            result = engine.reflect(rotated_reflection)
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
