"""Autonomous runtime orchestrator."""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.actuation_gateway import GatewayPath, gateway_path_from_receipt
from core.approval_inbox import ApprovalInbox, close_answered_effects_requests
from core.autonomous_runtime_proposals import AutonomousRuntimeProposals
from core.budget_governor import BudgetCounter, BudgetGovernor, BudgetLimits
from core.budget_kill_switch import BudgetKillSwitch, default_path
from core.campaign_verdict import against_start
from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from core.clarification_gate import clarification_for_replan_exhausted
from core.doc_routing import (
    is_confidence_evidence_diagnostic_question,
    is_doctrine_corporate_question,
)
from core.gateway_consult import (
    approval_paths,
    budget_ledger_snapshot,
    readiness_blockers,
)
from core.incident import IncidentLog
from core.ingestion import ingest_files
from core.learning_planner import LearningPlanner
from core.models import ToolCall
from core.note_contract import settle_note
from core.patch_route import settle_patch
from core.reflection import ReflectionConfig, ReflectionEngine
from core.run_context import run_restrictions
from core.success_check import observe_success_check
from core.task_lifecycle import (
    apply_run_exception,
    apply_run_outcome,
    task_heartbeat,
)
from core.task_queue import RuntimeTask, TaskAlreadyClaimed, TaskQueueStore
from core.tool_receipts import ReceiptPath, receipt_context

logger = logging.getLogger(__name__)


# Rotated per 10-min bucket so successive ticks study different subtrees; stateless, derived from wall clock.
_LEARN_ROOT_ROTATION: tuple[str, ...] = (".", "core", "tools", "tests", "scripts")
_REFLECTION_LOG_WINDOWS: tuple[int, ...] = (20, 40, 30, 60)
_PYTEST_EXIT_MEANING = {2: "interrupted", 3: "internal error", 4: "usage error", 5: "no tests collected"}

# Tools blocked run-scoped during a goal task when tests are disabled (include_tests=False); enforces operator intent, logged separately.
_NO_TESTS_BLOCKED_TOOLS: frozenset[str] = frozenset({"run_tests"})

# Tools that must NOT run on the unattended goal path regardless of dry-run (applied via PolicyGate.blocked_tools, run-scoped, always restored; the REPL is unaffected).
# spawn_subagent is never unattended; of the network tools only semantic_scholar_search stays
# blocked here (web_search, web_fetch, rss_fetch are open).
_AUTONOMOUS_GOAL_BLOCKED_TOOLS: frozenset[str] = frozenset(
    {
        "spawn_subagent",
        # find_in_files, python_probe and rss_fetch are open: they do not change the world.
        # semantic_scholar_search stays blocked: it is the only subject of the narrow per-run unblock (_UNBLOCKABLE_TOOLS).
        "semantic_scholar_search",
        # web_search / web_fetch are open (reading only); inbound content stays a hypothesis with a source.
        # memory_bank and journal_append are open by operator decision; their doors keep walls (provenance, write policy, readback,
        # per-process ceiling, daily voice cap tools/journal_append.VOICE_CALLS_PER_DAY).
    }
)


#: What an explicit config field may unblock for a goal run (learning action under a standing grant).
#: spawn_subagent and python_probe need their own gates; semantic_scholar_search remains so the narrow per-run unblock stays exercised.
_UNBLOCKABLE_TOOLS: frozenset[str] = frozenset({"semantic_scholar_search"})


def _goal_block_set(
    *, unblock_tools: frozenset[str], include_tests: bool,
) -> frozenset[str]:
    """Goal-path block list with the narrow unblock applied."""
    tests_block = _NO_TESTS_BLOCKED_TOOLS if not include_tests else frozenset()
    allowed = _UNBLOCKABLE_TOOLS & unblock_tools
    return (_AUTONOMOUS_GOAL_BLOCKED_TOOLS - allowed) | tests_block


def _rotation_index(modulus: int, *, bucket_seconds: int = 600) -> int:
    return int(time.time() // bucket_seconds) % max(modulus, 1)


#: Standing-grant usage journal: one line per permitted run.
_STANDING_USAGE_FILE = "standing_grant_usage.jsonl"

#: Why an effectful run has no permission; both gates are named because both were checked.
_NO_PERMISSION_REASONS: tuple[str, ...] = (
    "no per-run approval is pending or executed for this goal",
    "no standing grant is active (absent, expired, or spent for today)",
)


def _standing_usage_path(workspace: Any) -> Path:
    return Path(workspace or ".") / "data" / _STANDING_USAGE_FILE


def record_standing_grant_use(workspace: Any, grant_id: str) -> None:
    """One journal line per authority issued by a standing grant.

    Public because the grant has two consumers (this runtime and `core.rule_approved_apply`) and one usage journal.
    """
    from core.state_integrity import append_state_jsonl

    path = _standing_usage_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    append_state_jsonl(path, [{
        "grant_id": grant_id,
        "ts": datetime.now(timezone.utc).isoformat(),
    }])


#: Former internal name; the runtime and grant tests refer to it.
_record_standing_use = record_standing_grant_use


def _standing_runs_today_unlocked(path: Path, grant_id: str) -> int:
    """Today's usage count WITHOUT taking the lock: for calls from inside the lock."""
    from core.state_integrity import read_state_jsonl_unlocked

    if not path.is_file():
        return 0
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(
        1 for row in read_state_jsonl_unlocked(path)
        if str(row.get("grant_id")) == grant_id
        and str(row.get("ts") or "").startswith(today)
    )


def reserve_standing_grant_use(
    workspace: Any, grant_id: str, *, max_per_day: int
) -> bool:
    """Take one unit of the daily cap, or refuse; indivisible.

    Decision and write live under ONE lock of the file `append_state_jsonl` uses, so concurrent consumers cannot both pass `remaining=1`.
    A refusal means apply nothing.
    """
    from core.file_lock import exclusive_file_lock
    from core.state_integrity import append_state_jsonl_unlocked, state_lock_path

    if max_per_day <= 0:
        return False
    path = _standing_usage_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(state_lock_path(path)):
        if _standing_runs_today_unlocked(path, grant_id) >= max_per_day:
            return False
        append_state_jsonl_unlocked(path, [{
            "grant_id": grant_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        }])
        return True


def standing_runs_today(workspace: Any, grant_id: str) -> int:
    """How many runs this grant allowed today (UTC); the journal is the truth."""
    from core.file_lock import exclusive_file_lock
    from core.state_integrity import state_lock_path

    path = _standing_usage_path(workspace)
    if not path.is_file():
        return 0
    with exclusive_file_lock(state_lock_path(path)):
        return _standing_runs_today_unlocked(path, grant_id)


# --- Proposal hygiene: canonical signature + token-Jaccard semantic dedup ---
# A new proposal whose canonical token set overlaps a same-kind proposal at >= _PROPOSAL_JACCARD_THRESHOLD is rejected; kind is part of the signature.











# The data carriers live in core/autonomous_runtime_types; re-exported so existing imports keep working.
from core.autonomous_runtime_types import (  # noqa: E402
    AutonomousQueuedTaskReport,
    AutonomousQueueRunReport,
    AutonomousRunReport,
    AutonomousRunStatus,
    AutonomousRuntimeConfig,
    AutonomousTask,
    AutonomousTaskKind,
    AutonomousTaskReport,
    AutonomousTaskStatus,
)

#: Re-exported on purpose (a relocation, not an interface change); named here so the linter's unused-import is answered by code.
__all__ = [
    "AutonomousQueueRunReport",
    "AutonomousQueuedTaskReport",
    "AutonomousRunReport",
    "AutonomousRunStatus",
    "AutonomousRuntime",
    "AutonomousRuntimeConfig",
    "AutonomousTask",
    "AutonomousTaskKind",
    "AutonomousTaskReport",
    "AutonomousTaskStatus",
]


def _grant_is_live(expires_at: str | None, now: datetime) -> bool:
    """Whether an APPROVED permission is still within its term.

    `expire_stale` touches only `pending`, so approved requests are checked here, as `_active_standing_grant` does.
    No term still means unlimited (policy on irreversible effects belongs to the operator); an unreadable stamp is a refusal.
    """
    if not expires_at:
        return True
    try:
        deadline = datetime.fromisoformat(str(expires_at))
    except (TypeError, ValueError):
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline > now


def active_standing_grant(approval_inbox, workspace, *, log=None):
    """One grant check for the whole project, so the tick asks about the same permission."""
    now = datetime.now(timezone.utc)
    for item in approval_inbox.list(status="approved"):
        if item.operation != "autonomous_runtime.standing_grant":
            continue
        expires = str(item.expires_at or "")
        try:
            if expires and datetime.fromisoformat(expires) <= now:
                continue
        except ValueError:
            continue
        cap = int((item.payload or {}).get("max_runs_per_day") or 0)
        if cap <= 0:
            continue
        if standing_runs_today(workspace, item.id) >= cap:
            if log is not None:
                log("standing_grant_exhausted", {"approval_id": item.id, "cap": cap})
            continue
        return item
    return None


def standing_grant_remaining_runs(workspace: Any, grant: Any) -> int:
    """How many authorities this grant has LEFT today, from the shared usage journal (one cap for all consumers)."""
    cap = int((getattr(grant, "payload", None) or {}).get("max_runs_per_day") or 0)
    if cap <= 0:
        return 0
    return max(0, cap - standing_runs_today(workspace, str(getattr(grant, "id", ""))))


class AutonomousRuntime(AutonomousRuntimeProposals):
    """Run a bounded autonomous project-health pass."""

    def __init__(
        self,
        agent: Any,
        *,
        workspace: Any,
        approval_inbox: ApprovalInbox | None = None,
        incident_log: IncidentLog | None = None,
        receipt_path: ReceiptPath = "runtime",
    ):
        self.agent = agent
        self.workspace = workspace
        self.approval_inbox = approval_inbox or ApprovalInbox()
        self.incident_log = incident_log
        self.receipt_path = receipt_path

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
            try:
                task_queue.mark_running(task.id)
            except TaskAlreadyClaimed:
                # the list was read once; another consumer may have taken this task: skip, and journal it
                self._log("task_claim_lost", {"task_id": task.id})
                continue
            try:
                # the heartbeat lets startup recovery tell a killed process from a slow one; `task_id` is bound as a default,
                # NOT read from `task`, because the callback can fire after the loop advanced
                with task_heartbeat(
                    task_queue,
                    task.id,
                    on_error=lambda exc, task_id=task.id: self._log(
                        "task_heartbeat_failed",
                        {"task_id": task_id, "error": type(exc).__name__},
                    ),
                ):
                    run_report = self.run(
                        _config_from_task(task),
                        budget=queue_budget,
                        circuit=queue_circuit,
                    )
            except Exception as exc:  # noqa: BLE001 — the failure is recorded and logged
                failed, decision = apply_run_exception(task_queue, task.id, exc)
                self._log(
                    "task_lifecycle",
                    {"task_id": task.id, "status": failed.status,
                     **decision.to_log_payload()},
                )
                processed.append(
                    AutonomousQueuedTaskReport(
                        task_id=failed.id,
                        goal=failed.goal,
                        status=failed.status,
                        summary=failed.last_error,
                    )
                )
                continue

            updated, decision = apply_run_outcome(
                task_queue,
                task.id,
                status=run_report.status,
                stop_reason=run_report.stop_reason,
                report=run_report.to_dict(),
                # outcome, not lifecycle, but only when the run HAD work to do
                work_done=(
                    run_report.semantic_result()[1]
                    if any(t.task.kind in ("goal", "propose", "tests") for t in run_report.tasks)
                    else None
                ),
            )
            self._log(
                "task_lifecycle",
                {"task_id": task.id, "status": updated.status,
                 "run_status": run_report.status, **decision.to_log_payload()},
            )
            if decision.outcome == "done":
                processed.append(
                    AutonomousQueuedTaskReport(
                        task_id=updated.id,
                        goal=updated.goal,
                        status=updated.status,
                        run_status=run_report.status,
                        summary=f"tasks={len(run_report.tasks)}",
                    )
                )
                continue

            processed.append(
                AutonomousQueuedTaskReport(
                    task_id=updated.id,
                    goal=updated.goal,
                    status=updated.status,
                    run_status=run_report.status,
                    summary=updated.last_error,
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

        # before asking, check whether a permission is already granted; an approved permission is ONE-TIME (executed), the §9 right stays with the human
        if not config.dry_run and not config.effects_approved:
            granted = self._granted_effects_approval(config)
            if granted is not None:
                config = replace(config, effects_approved=True)
                self.approval_inbox.mark_executed(granted.id)
                self._log("autonomous_effects_granted", {"approval_id": granted.id})
        # standing grant: one yes a week feeds the automaton within walls (daily run cap, term, usage journal); it is NOT marked executed
        if not config.dry_run and not config.effects_approved:
            standing = self._active_standing_grant()
            if standing is not None:
                # take the unit and only then permit effects: a second consumer fits between the remaining check and the usage write
                cap = int((standing.payload or {}).get("max_runs_per_day") or 0)
                if reserve_standing_grant_use(
                    self.workspace, standing.id, max_per_day=cap
                ):
                    config = replace(config, effects_approved=True)
                    self._log("autonomous_effects_standing", {
                        "approval_id": standing.id,
                        "runs_today": standing_runs_today(self.workspace, standing.id),
                        "requests_answered": close_answered_effects_requests(
                            self.approval_inbox, standing.id),
                    })
                else:
                    self._log("standing_grant_exhausted", {
                        "approval_id": standing.id, "cap": cap,
                    })
        if not config.dry_run and not config.effects_approved:
            _pending_before = {i.id for i in self.approval_inbox.pending()}
            item = self.approval_inbox.add(
                operation="autonomous_runtime.allow_effects",
                summary=(
                    "This run wants to apply effects and holds no permission: "
                    "neither a per-run approval nor an active standing grant."
                ),
                risk="irreversible",
                reasons=_NO_PERMISSION_REASONS,
                payload={
                    "goal": config.goal,
                    "dry_run": False,
                    "limit": config.limit,
                    "include_tests": config.include_tests,
                    "learning_limit": config.learning_limit,
                    # without these two an approved run lost its GOAL and degraded to a health pass
                    "include_goal": config.include_goal,
                    "include_proposals": config.include_proposals,
                },
                # the dedup key is a HASH of the goal, not raw text: stored payloads are redacted, and sensitive goal text must not live in a durable key; a decided item stops deduping
                dedup_key=self._effects_dedup_key(config.goal),
            )
            if item.id not in _pending_before:
                # a dedup hit adds nothing and must not burn request budget
                budget.reserve(
                    "approval_requests", reason="non-dry-run runtime approval"
                )
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
            self._log("autonomous_runtime_stop", report.for_log())
            return report

        with receipt_context(
            trace_id=self._receipt_trace_id(),
            path=self.receipt_path,
            workspace=self.workspace,
        ):
            queue = self._build_queue(config)[: config.limit]
            budget_stop = ""
            for task in queue:
                allowed = circuit.check()
                if not allowed.allowed:
                    break
                if not self._reserve(budget, circuit, "cycles", task.kind):
                    budget_stop = f"cycles budget exhausted before task: {task.kind}"
                    break
                usage_before = self._usage_counters()
                report = self._run_task(task, config, budget, circuit)
                self._reserve_usage_delta(budget, circuit, usage_before, task.kind)
                tasks.append(report)
                self._log("autonomous_task_result", report.to_dict())
                if report.status == "failed":
                    circuit.record_failure(report.summary)
                elif report.status == "done" and task.kind not in ("status", "learn"):
                    # a gate question and the state probe are not success; the breaker is fed only by WORK tasks
                    circuit.record_success()

        circuit_decision = circuit.check()
        status: AutonomousRunStatus = "completed" if circuit_decision.allowed else "stopped"
        stop_reason = "" if circuit_decision.allowed else circuit_decision.reason
        if status == "completed" and budget_stop:  # denial below the breaker threshold
            status, stop_reason = "stopped", budget_stop
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

        if config.enable_self_build:
            self._run_self_build_proposal(config)

        if status == "stopped":
            self._record_incident(stop_reason, tasks)

        self._log("autonomous_runtime_stop", report.for_log())
        return report

    def _record_incident(
        self,
        stop_reason: str,
        tasks: list[AutonomousTaskReport],
    ) -> None:
        """Open a structured incident when the circuit halts the run; best-effort, de-duplicated, never raises into the run path."""
        log = self.incident_log
        if log is None:
            return
        trigger = "autonomous_run_stopped"
        affected = "core.autonomous_runtime"
        try:
            failed = sum(1 for t in tasks if t.status == "failed")
            for inc in log.open_incidents():
                if inc.trigger == trigger and inc.affected_module == affected:
                    # a repeat stop must not vanish: the incident record grows
                    note = (inc.postmortem_note + "; " if inc.postmortem_note else "")
                    log.update(inc.id, postmortem_note=(
                        note + f"recurred {datetime.now(timezone.utc).isoformat()}: "
                        f"{stop_reason[:120]}"
                    )[-2000:])
                    self._log("incident_recurred", {
                        "incident_id": inc.id, "stop_reason": stop_reason,
                        "failed_tasks": failed,
                    })
                    return
            log.open_incident(
                severity="high",
                trigger=trigger,
                affected_module=affected,
                containment_action=(
                    "halted autonomous run; remaining queued tasks not executed"
                ),
            )
            self._log(
                "incident_opened",
                {
                    "trigger": trigger,
                    "stop_reason": stop_reason,
                    "failed_tasks": failed,
                },
            )
        except Exception as exc:  # noqa: BLE001 — reason stated above
            # failing silently here would leave a halt with no incident
            self._log(
                "incident_record_failed",
                {
                    "trigger": trigger,
                    "stop_reason": stop_reason,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:200],
                },
            )
            return

    def status(self) -> dict:
        return {
            "approval_inbox": self.approval_inbox.digest(),
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
                return self._task_goal(task, config)
            if task.kind == "propose":
                return self._task_propose(task, config, budget)
        except Exception as exc:  # noqa: BLE001 — reason stated above
            # not silence: the failure leaves as the task's own report; broad on purpose, one task must not take down the whole run
            return AutonomousTaskReport(
                task,
                "failed",
                f"{type(exc).__name__}: {exc}",
                {"error_type": type(exc).__name__},
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
        if (
            is_doctrine_corporate_question(config.goal)
            or is_confidence_evidence_diagnostic_question(config.goal)
        ):
            rotated_root = "."
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
            auto_write_memory=config.learning_writes_memory,
            require_verified=True,
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
        timed_out = bool(output.get("timed_out"))
        exit_code = output.get("exit_code")
        # a timed-out run (or no exit code) established nothing: report inconclusive, never success
        if timed_out or exit_code is None:
            status: AutonomousTaskStatus = "inconclusive"
            summary = (
                f"inconclusive (timed_out={timed_out}, exit_code={exit_code}); "
                f"passed={passed} failed_or_errors={failed}"
            )
        elif exit_code != 0:
            # Only exit code 0 is a pass; 5 (nothing collected) established nothing.
            status = "inconclusive" if exit_code == 5 else "failed"
            meaning = _PYTEST_EXIT_MEANING.get(exit_code, "tests failed")
            summary = f"{meaning} (exit_code={exit_code}); passed={passed} failed_or_errors={failed}"
        else:
            status = "done" if failed == 0 else "failed"
            summary = f"passed={passed} failed_or_errors={failed}"
        return AutonomousTaskReport(
            task,
            status,
            summary,
            {
                "exit_code": exit_code,
                "timed_out": timed_out,
                "passed": passed,
                "failed": output.get("failed", 0),
                "errors": output.get("errors", 0),
                "failed_tests": output.get("failed_tests", []),
                # which code was checked: the tool returns it and the report must keep it
                "code_state": output.get("code_state"),
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
            except Exception as exc:  # noqa: BLE001 — reason stated above
                # 0 stays (an int is expected), but "counted none" and "could not count" are told apart only by the event
                web_fetches = 0
                self._log(
                    "usage_counter_failed",
                    {"counter": "web_fetches", "error_type": type(exc).__name__},
                )
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
        except Exception as exc:  # noqa: BLE001 — reason stated above
            # "the store holds 0 records" and "could not be read" are different facts
            self._log(
                "memory_count_failed",
                {"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return 0

    def _gateway_path(self) -> GatewayPath:
        return gateway_path_from_receipt(self.receipt_path)

    def _gate_question_report(self, task: AutonomousTask, answer: str) -> AutonomousTaskReport:
        """Report for a turn where the loop answered with a question, not work."""
        question = str(answer or "")
        self._log(
            "clarification_gate",
            {"question": question[:500], "path": self.receipt_path, "trigger": "gate_question"},
        )
        return AutonomousTaskReport(
            task,
            "clarify",
            (question[:120].replace("\n", " ") or "clarification required"),
            {
                "answer": answer,
                "clarification": {"question": question, "trigger": "gate_question"},
                "stop_reason": "clarification_gate",
                "recommended_action": "enter_clarify_mode",
            },
        )

    def _task_goal(self, task: AutonomousTask, config: AutonomousRuntimeConfig) -> AutonomousTaskReport:
        """Run task.description as a user question through the parent AgentLoop.

        Dry-run blocking is handled by the actuation gateway; posture blocks (``_AUTONOMOUS_GOAL_BLOCKED_TOOLS``) always apply,
        plus ``run_tests`` when tests are disabled. Gateway and policy blocks are run-scoped and always restored, even on error.
        """
        self._goal_started_at = time.time() - 2.0  # mtime on Linux is coarser than time.time()
        policy = getattr(self.agent, "policy", None)
        has_block_support = policy is not None and hasattr(policy, "blocked_tools")
        to_block = _goal_block_set(
            unblock_tools=config.unblock_tools, include_tests=config.include_tests,
        )
        if config.unblock_tools:
            self._log("goal_tools_unblocked", {
                "unblocked": sorted(_UNBLOCKABLE_TOOLS & config.unblock_tools),
            })
        block = has_block_support and bool(to_block)
        previous_gateway_path: GatewayPath = getattr(self.agent, "gateway_path", "repl")
        previous_gateway_kill_switch = getattr(self.agent, "gateway_kill_switch", None)
        previous_gateway_budget_snapshot = getattr(self.agent, "gateway_budget_snapshot", None)
        previous_gateway_readiness_blockers = getattr(
            self.agent, "gateway_readiness_blockers", ()
        )
        previous_gateway_check_readiness = bool(
            getattr(self.agent, "gateway_check_readiness", False)
        )
        previous_suppress_learning_writes = bool(
            getattr(self.agent, "suppress_durable_learning_writes", False)
        )
        gateway_path = self._gateway_path()
        snapshot = budget_ledger_snapshot(self.workspace)
        self.agent.gateway_path = gateway_path
        self.agent.gateway_kill_switch = BudgetKillSwitch(
            path=default_path(self.workspace)
        )
        self.agent.gateway_budget_snapshot = snapshot
        self.agent.gateway_check_readiness = not bool(config.dry_run)
        self.agent.suppress_durable_learning_writes = (
            previous_suppress_learning_writes or bool(config.dry_run)
        )
        self.agent.gateway_readiness_blockers = (
            readiness_blockers(
                pending_approvals=len(self.approval_inbox.pending()),
                budget_snapshot=snapshot,
            )
            if not config.dry_run
            else ()
        )
        # what the pending requests touch; an empty set means unknown and keeps the ban general
        self.agent.gateway_approval_paths = approval_paths(self.approval_inbox)
        # the planner-visible tool surface is pruned by the same run context (`effective_hidden_tools`); a neighbouring run's cleanup cannot widen it mid-run
        self._log(
            "autonomous_goal_gateway",
            {
                "gateway_dry_run": bool(config.dry_run),
                "gateway_path": gateway_path,
                "blocked_tools": sorted(to_block),
                "no_tests": not config.include_tests,
                "durable_learning_writes_suppressed": bool(config.dry_run),
            },
        )
        try:
            # no task_id here: the RuntimeTask was reduced to kind+description; the run's own narrowing lives in the run context
            # (`run_restrictions`), so this task cannot lift a host limit nor have its own lifted by a neighbouring run
            with run_restrictions(
                blocked_tools=to_block if block else frozenset(),
                dry_run=bool(config.dry_run),
            ):
                # the gate flag belongs to ONE run: inheriting it would paint a real answer as a question
                self.agent.last_answer_was_clarification = False
                answer = self.agent.run(user_question=task.description)
        finally:
            self.agent.gateway_path = previous_gateway_path
            self.agent.gateway_kill_switch = previous_gateway_kill_switch
            self.agent.gateway_budget_snapshot = previous_gateway_budget_snapshot
            self.agent.gateway_readiness_blockers = previous_gateway_readiness_blockers
            self.agent.gateway_check_readiness = previous_gateway_check_readiness
            self.agent.suppress_durable_learning_writes = previous_suppress_learning_writes
        # a loop-gate question is not work: status "clarify", question in details['clarification'], same shape as the replan_exhausted branch below
        if bool(getattr(self.agent, "last_answer_was_clarification", False)):
            return self._gate_question_report(task, answer)
        replan_exhausted = bool(getattr(self.agent, "last_replan_exhausted", False))
        if replan_exhausted:
            clarify = clarification_for_replan_exhausted()
            clarify_payload = clarify.to_dict()
            self._log(
                "clarification_gate",
                {
                    **clarify_payload,
                    "path": self.receipt_path,
                    "trigger": "replan_exhausted",
                },
            )
            summary = (clarify.prompt() or "clarification required (replan exhausted)")[
                :120
            ].replace("\n", " ")
            return AutonomousTaskReport(
                task,
                "clarify",
                summary,
                {
                    "answer": answer,
                    "clarification": clarify_payload,
                    "stop_reason": "replan_exhausted",
                    "recommended_action": "enter_clarify_mode",
                },
            )
        if not str(answer or "").strip():
            # an empty answer is "inconclusive", not done
            return AutonomousTaskReport(task, "inconclusive", "(no answer)", {"answer": answer})
        return self._settle_goal_answer(task, answer, config)

    def _settle_goal_answer(
        self, task: AutonomousTask, answer: str, config: AutonomousRuntimeConfig
    ) -> AutonomousTaskReport:
        """Decide the goal outcome by the world, not by the answer text.

        The success criterion is free text; only the part readable from disk is observed (core/success_check.py).
        'unverifiable' keeps the previous behaviour and names it: not checked is not met.
        """
        # a trace older than the goal start was not work (`core/campaign_verdict.py`)
        observation = against_start(observe_success_check(config.goal_success_check, self.workspace),
                                    self.workspace, getattr(self, "_goal_started_at", None))
        observation.update(settle_patch(self.agent, self.workspace, config.goal_success_check)
                           or settle_note(self.workspace, config.goal_success_check) or {})
        details = {
            "answer": answer,
            "success_check": config.goal_success_check,
            "success_check_verdict": observation["verdict"],
            "success_check_artifacts": observation["artifacts"],
            "success_check_missing": observation["missing"],
            "success_check_observed": observation["observed"],
        }
        self._log("goal_success_check", {
            "goal": config.goal[:200],
            "verdict": observation["verdict"],
            "reason": observation["reason"],
        })
        if observation["verdict"] in ("missing", "preexisting"):
            missing = ", ".join(observation["missing"] or observation["artifacts"])
            return AutonomousTaskReport(
                task,
                "inconclusive",
                f"ответ есть, следа нет: {missing}"[:120],
                details,
            )
        return AutonomousTaskReport(
            task,
            "done",
            answer[:120].replace("\n", " "),
            details,
        )





    def _active_standing_grant(self):
        """The active standing grant with a remainder for today, or None."""
        return active_standing_grant(
            self.approval_inbox, self.workspace, log=self._log
        )


    def _granted_effects_approval(self, config: AutonomousRuntimeConfig):
        """The approved permission for THIS same goal, or None; matched by the key the request is filed under, oldest first."""
        key = self._effects_dedup_key(config.goal)
        now = datetime.now(timezone.utc)
        return next(
            (i for i in self.approval_inbox.list(status="approved")
             if i.operation == "autonomous_runtime.allow_effects"
             and (i.payload or {}).get("dedup_key") == key
             and _grant_is_live(i.expires_at, now)),
            None,
        )

    @staticmethod
    def _effects_dedup_key(goal: str) -> str:
        """One key per SUBJECT of the goal, not per wording; a goal without a file keys as before.

        The permission is run-level (a patch below still needs `self_apply_lane.run` approval).
        Case is folded ONLY here: `_named_target` feeds `target_path`, which is opened, and folding there would break POSIX.
        """
        from core.best_next_action_helpers import _named_target

        subject = _named_target(core := (goal or "").split("\n\n", 1)[0])  # campaign notes follow a blank line
        basis = f"subject:{subject.casefold()}" if subject else core
        digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
        return f"autonomous_runtime.allow_effects:{digest}"

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
        # honour the operator brake: when AGENT_FREEZE_AUTO_MEMORY froze 'agent-auto' writes, reflection lessons are frozen too (derived from the live write policy)
        write_policy = getattr(self.agent, "write_policy", None)
        frozen_sources = getattr(write_policy, "frozen_sources", frozenset())
        freeze_writes = config.dry_run or "agent-auto" in frozen_sources
        engine = ReflectionEngine(
            workspace=self.workspace,
            persistent_memory=persistent_store,
            llm=llm,
            log_dir=log_dir,
            logger=log,
            freeze_writes=freeze_writes,
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
            # consume the LearningPlan: ingest the files the reflection engine named as weak spots
            if result.learning_plan and result.learning_plan.source_paths:
                try:
                    ingest = ingest_files(
                        agent=self.agent,
                        workspace=self.workspace,
                        paths=result.learning_plan.source_paths,
                        dry_run=config.dry_run,
                        auto_write_memory=config.learning_writes_memory,
                        require_verified=True,
                    )
                    grounding = result.learning_grounding or {}
                    unmet = [
                        *grounding.get("phantom", ()),
                        *grounding.get("unresolvable", ()),
                    ]
                    self._log(
                        "reflection_learning_ingest",
                        {
                            "sources": len(result.learning_plan.source_paths),
                            "claims": ingest.claim_count,
                            "conflicts": ingest.conflicts,
                            "dry_run": config.dry_run,
                            # `grounding` separates "weak spot studied" from a mere ingested-file count, so a substitution cannot read as success
                            "grounding": grounding.get("status", "none"),
                            "unmet_targets": unmet or None,
                        },
                    )
                except Exception as ingest_exc:  # noqa: BLE001 — the failure is recorded and logged
                    self._log(
                        "reflection_learning_ingest_error",
                        {"error": f"{type(ingest_exc).__name__}: {ingest_exc}"},
                    )
            return result.to_dict()
        except Exception as exc:  # noqa: BLE001 — the failure is recorded and logged
            self._log("reflection_error", {"error": f"{type(exc).__name__}: {exc}"})
            return {"error": str(exc)}



    def _receipt_trace_id(self) -> str:
        log = getattr(self.agent, "log", None)
        if log is not None:
            return str(getattr(log, "trace_id", "") or "")
        return ""

    def _log(self, event: str, payload: Any) -> None:
        log = getattr(self.agent, "log", None)
        if log is not None:
            log.log(event, payload)


#: Queue kinds the runtime can execute; `resume_checkpoint` is here because the budget guard writes one for the unattended path.
_RUNNABLE_TASK_KINDS: frozenset[str] = frozenset({"auto_run", "resume_checkpoint"})


def _config_from_task(task: RuntimeTask) -> AutonomousRuntimeConfig:
    """Turn a claimed queue row into a run configuration; a `resume_checkpoint` runs as a RE-RUN of its goal (exact resumption is the human `--resume` path)."""
    if task.kind not in _RUNNABLE_TASK_KINDS:
        raise ValueError(f"unsupported runtime task kind: {task.kind}")
    return AutonomousRuntimeConfig(
        goal=task.goal,
        dry_run=task.dry_run,
        limit=task.limit,
        include_tests=task.include_tests,
        learning_limit=task.learning_limit,
        # a queued goal must EXECUTE, not be carried and dropped; `_build_queue` still refuses the default placeholder goal and
        # `_task_goal` still applies posture blocks, gateway dry-run and the agent-runs budget
        include_goal=True,
    )
