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
from core.approval_inbox import ApprovalInbox
from core.autonomous_runtime_proposals import AutonomousRuntimeProposals
from core.budget_governor import BudgetCounter, BudgetGovernor, BudgetLimits
from core.budget_kill_switch import BudgetKillSwitch, default_path
from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from core.clarification_gate import clarification_for_replan_exhausted
from core.doc_routing import (
    is_confidence_evidence_diagnostic_question,
    is_doctrine_corporate_question,
)
from core.gateway_consult import budget_ledger_snapshot, readiness_blockers
from core.incident import IncidentLog
from core.ingestion import ingest_files
from core.learning_planner import LearningPlanner
from core.models import ToolCall
from core.reflection import ReflectionConfig, ReflectionEngine
from core.run_context import run_restrictions
from core.task_lifecycle import (
    apply_run_exception,
    apply_run_outcome,
    task_heartbeat,
)
from core.task_queue import RuntimeTask, TaskAlreadyClaimed, TaskQueueStore
from core.tool_receipts import ReceiptPath, receipt_context

logger = logging.getLogger(__name__)


# Rotated per 10-min bucket so successive auto-run ticks study different
# subtrees and reflect over different log windows instead of repeating the
# same input. Stateless: derived from wall clock.
_LEARN_ROOT_ROTATION: tuple[str, ...] = (".", "core", "tools", "tests", "scripts")
_REFLECTION_LOG_WINDOWS: tuple[int, ...] = (20, 40, 30, 60)

# Tools that must NOT run during a goal task when the operator disabled tests
# (include_tests=False, e.g. :work-session or :auto-run --no-tests). Gating the
# dedicated "tests" task is not enough: the planner can still select run_tests
# while executing the goal via the parent AgentLoop. Blocking it run-scoped
# makes the planner replan onto a path that honors --no-tests. This is not an
# effect-only block; it enforces operator intent, so it is logged separately.
_NO_TESTS_BLOCKED_TOOLS: frozenset[str] = frozenset({"run_tests"})

# Tools that must NOT run on the unattended autonomous / daemon goal path,
# regardless of dry-run. These enforce Stage-0 posture that the free planner
# would otherwise violate on the goal task:
#   - spawn_subagent: the subagent runner is Stage 6, never unattended — an
#     auto-run / daemon goal must not spawn child agents.
#   - network egress: ЧТЕНИЕ веба (web_search / web_fetch) открыто с
#     2026-09-01 по слову оператора — агент сам потянулся за чужим решением
#     своей же проблемы, и ключ висел не на той двери. Остальной выход наружу
#     (rss_fetch / semantic_scholar_search) закрыт: их никто не просил, и одни
#     ворота за раз.
# Applied via PolicyGate.blocked_tools exactly like _NO_TESTS_BLOCKED_TOOLS:
# run-scoped and always restored. The interactive REPL path is unaffected.
_AUTONOMOUS_GOAL_BLOCKED_TOOLS: frozenset[str] = frozenset(
    {
        "spawn_subagent",
        # web_search / web_fetch БОЛЬШЕ НЕ ЗДЕСЬ — решение оператора 2026-09-01,
        # дословно: «разреши ему смотреть в интернет, когда он сам захотел».
        #
        # Замер, вызвавший решение. Впервые увидев свои открытые дела, агент
        # взял целью собственный дефект («передача значения между шагами») и
        # САМ потянулся за чужим решением: планировщик выбрал web_search с
        # запросом "step-to-step value-passing contract tool output arguments
        # defect". Ворота отказали — потому что чтение веба открывалось не по
        # намерению агента, а по ФОРМУЛИРОВКЕ цели (глагол изучения плюс слово
        # про веб). Ключ висел на другой двери: инициатива была, доступа не
        # было.
        #
        # Открыто ровно чтение и ровно два инструмента. Оба не меняют мир:
        # поиск и выкачка страницы. Всё, что приходит снаружи, остаётся
        # ГИПОТЕЗОЙ с источником — это уже правило дома, а не новая уступка;
        # защита от инъекций и классификация данных стоят на прежнем месте.
        "rss_fetch",
        "semantic_scholar_search",
        # python_probe: лаборатория исполняет код — безнадзорный путь остаётся
        # repo-local и read-only; открывать ей этот путь — отдельное решение
        # (одни ворота за раз), не побочный эффект её постройки 2026-08-16.
        "python_probe",
        # memory_bank: вердикт агента 2026-08-31 (unattended_verdict.md, BLOCKED):
        # «память — то, что я потом читаю как истину; чтение не фильтруется» —
        # самоотравление перевешивает ценность автономного банкования, пока
        # нет фильтра на чтении. Его собственное решение о его же памяти.
        "memory_bank",
        # journal_append: вердикт агента 2026-09-01 (append_tool_design.md ред.2,
        # BLOCKED): «реестр — тоже то, что я потом читаю как истину; он кормит
        # выбиратель кампании; существенной разницы с памятью нет» — по его же
        # прецеденту memory_bank, третье самоограничение.
        "journal_append",
    }
)


#: Что вообще МОЖНО разблокировать целевому прогону явным полем конфига
#: (учебное действие под стоячим грантом, решение оператора 2026-08-16).
#: spawn_subagent и python_probe этим полем не разблокируемы по построению —
#: им нужны собственные ворота.
#:
#: 2026-09-01: web_search/web_fetch ушли отсюда не потому, что запрещены, а
#: потому что открыты ВСЕГДА (слово оператора). Разблокировать осталось поиск
#: научных работ — то же чтение внешнего мира, и учебное действие открывает
#: его той же дверью. Поле не бездействует: механизм узкой, НЕ переживающей
#: прогон разблокировки обязан оставаться проверяемым живым инструментом.
_UNBLOCKABLE_TOOLS: frozenset[str] = frozenset({"semantic_scholar_search"})


def _goal_block_set(
    *, unblock_tools: frozenset[str], include_tests: bool,
) -> frozenset[str]:
    """Список блокировок целевого пути с учётом узкой разблокировки."""
    tests_block = _NO_TESTS_BLOCKED_TOOLS if not include_tests else frozenset()
    allowed = _UNBLOCKABLE_TOOLS & unblock_tools
    return (_AUTONOMOUS_GOAL_BLOCKED_TOOLS - allowed) | tests_block


def _rotation_index(modulus: int, *, bucket_seconds: int = 600) -> int:
    return int(time.time() // bucket_seconds) % max(modulus, 1)


#: Журнал потребления стоячих грантов: одна строка — один пропущенный прогон.
_STANDING_USAGE_FILE = "standing_grant_usage.jsonl"

#: Why an effectful run has no permission. Both gates are named because both
#: were checked: docs/CODE_NOTES.md, «The request that described a stage that
#: ended».
_NO_PERMISSION_REASONS: tuple[str, ...] = (
    "no per-run approval is pending or executed for this goal",
    "no standing grant is active (absent, expired, or spent for today)",
)


def _standing_usage_path(workspace: Any) -> Path:
    return Path(workspace or ".") / "data" / _STANDING_USAGE_FILE


def _record_standing_use(workspace: Any, grant_id: str) -> None:
    """Одна строка журнала на один прогон, пропущенный стоячим грантом."""
    from core.state_integrity import append_state_jsonl

    path = _standing_usage_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    append_state_jsonl(path, [{
        "grant_id": grant_id,
        "ts": datetime.now(timezone.utc).isoformat(),
    }])


def standing_runs_today(workspace: Any, grant_id: str) -> int:
    """Сколько прогонов этот грант пропустил сегодня (UTC). Журнал — истина."""
    from core.state_integrity import read_state_jsonl

    path = _standing_usage_path(workspace)
    if not path.is_file():
        return 0
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(
        1 for row in read_state_jsonl(path)
        if str(row.get("grant_id")) == grant_id
        and str(row.get("ts") or "").startswith(today)
    )


# --- Proposal hygiene: canonical signature + token-Jaccard semantic dedup ---
#
# sha256(description) only catches verbatim repeats. Sonnet rewords the same
# idea every tick, so the inbox accumulates near-duplicates. Below we extract
# a canonical token set per proposal and reject new proposals whose tokens
# overlap an existing same-kind proposal at >= _PROPOSAL_JACCARD_THRESHOLD.
# Kind is part of the signature: a "learn" claim-distribution and a "goal"
# claim-distribution stay separate (they imply different work).











# The data carriers moved to core/autonomous_runtime_types on 2026-08-22
# (operator: a file too large to read through produces mistakes). Re-exported
# here so every existing import keeps working — a relocation, not an
# interface change. Explanation: docs/CODE_NOTES.md.
from core.autonomous_runtime_types import (  # noqa: E402 — kept where the
    AutonomousQueuedTaskReport,  # definitions used to live, so reading order
    AutonomousQueueRunReport,  # is unchanged for anyone walking this file
    AutonomousRunReport,
    AutonomousRunStatus,
    AutonomousRuntimeConfig,
    AutonomousTask,
    AutonomousTaskKind,
    AutonomousTaskReport,
    AutonomousTaskStatus,
)

#: Re-exported ON PURPOSE: every existing `from core.autonomous_runtime import
#: AutonomousTask` keeps working, so the move is a relocation and not an
#: interface change. Named here so a linter's "unused import" is answered by
#: the code rather than by a suppression comment.
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
    """Действует ли ОДОБРЕННОЕ разрешение по своему сроку.

    H-41 в docs/audit/HISTORICAL_FAILURE_LEDGER.md. Разрешение на НЕОБРАТИМЫЕ
    эффекты искалось среди `list(status="approved")`, а этот читатель истёкшие
    не отсекает: `expire_stale` трогает только `pending`. Замер 2026-08-24 —
    заявка со сроком, прошедшим тридцать дней назад, невидима для `pending()` и
    полностью видима здесь, то есть продолжает разрешать.

    Смысл поля не выдуман: сосед по файлу, `_active_standing_grant`, уже читает
    `expires_at` у одобренной заявки как срок действия гранта и пропускает
    истёкшие. Два механизма разрешения расходились ровно на защите; здесь
    второй приводится к первому.

    Отсутствие срока — по-прежнему «бессрочно»: так ведёт себя разрешение,
    заведённое без даты, и менять ЭТО значило бы вводить политику, а политика
    о необратимом принадлежит оператору. Нечитаемая отметка — отказ: пропустить
    необратимое действие по непрочитанному сроку хуже, чем попросить новое
    одобрение.
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
    """Одна проверка гранта на весь проект: вторая разошлась бы с первой.

    Вынесена, чтобы тик спрашивал о том же разрешении (класс H-26 аудита).
    """
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
                # The list was read once, up front; by the time we reach a task
                # in the tail another consumer may have taken it. Skipping is
                # the correct outcome, and it is journaled rather than silent.
                self._log("task_claim_lost", {"task_id": task.id})
                continue
            try:
                # The heartbeat is what lets startup recovery tell a killed
                # process from a slow one (MIR-040); without it a long run and
                # an abandoned row are the same observation.
                # `task_id` is bound as a default, NOT read from `task`: the
                # heartbeat runs on a background thread and `__exit__` joins it
                # with a bounded timeout, so this callback can still fire after
                # the loop has advanced. A closure over the loop variable would
                # then blame whichever task is current — sending a reader to the
                # wrong task for a failure that belongs to this one.
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
                # A2 (2026-09-03): исход, не жизненный цикл — но только когда
                # прогону БЫЛО что делать: проход здоровья (status/learn) без
                # рабочих задач честно «done», а не «без работы».
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

        # Прежде чем просить — спросить, не дано ли уже: «да» оператора никто
        # не читал, и каждый прогон заводил новую заявку с тем же ключом.
        # Разрешение ОДНОРАЗОВОЕ (executed) — право §9 остаётся у человека.
        # docs/CODE_NOTES.md, «The approval nobody read».
        if not config.dry_run and not config.effects_approved:
            granted = self._granted_effects_approval(config)
            if granted is not None:
                config = replace(config, effects_approved=True)
                self.approval_inbox.mark_executed(granted.id)
                self._log("autonomous_effects_granted", {"approval_id": granted.id})
        # Стоячий грант: одно «да» на неделю питает автомат в стенах —
        # дневной лимит прогонов, срок, журнал потребления; права §9 целы.
        # Грант НЕ помечается executed: он живёт до истечения или отзыва.
        # Зачем: docs/CODE_NOTES.md, «One yes a week».
        if not config.dry_run and not config.effects_approved:
            standing = self._active_standing_grant()
            if standing is not None:
                config = replace(config, effects_approved=True)
                _record_standing_use(self.workspace, standing.id)
                self._log("autonomous_effects_standing", {
                    "approval_id": standing.id,
                    "runs_today": standing_runs_today(self.workspace, standing.id),
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
                    # Без этих двух одобренный прогон терял ЦЕЛЬ и вырождался
                    # в health-pass: docs/CODE_NOTES.md, «The approved goal
                    # that never ran».
                    "include_goal": config.include_goal,
                    "include_proposals": config.include_proposals,
                },
                # MIR-072 (measured live 2026-08-03): sixteen `:work-session`
                # retries piled up sixteen identical pending items because this
                # call site never consulted the inbox's own duplicate guard.
                # Same goal → same pending request; a decided item stops
                # deduping, so a new run may ask again. The key is a HASH of
                # the goal, not the raw text: the stored payload is redacted,
                # so a raw-text key would stop matching exactly when the goal
                # carries a secret (review round #284), and long/sensitive goal
                # text has no business living inside a durable key.
                dedup_key=self._effects_dedup_key(config.goal),
            )
            if item.id not in _pending_before:
                # A dedup hit adds nothing and must not burn request budget
                # (review round #284).
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
            self._log("autonomous_runtime_stop", report.to_dict())
            return report

        with receipt_context(
            trace_id=self._receipt_trace_id(),
            path=self.receipt_path,
            workspace=self.workspace,
        ):
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
                elif report.status == "done" and task.kind not in ("status", "learn"):
                    # A8 (2026-09-03): вопрос ворот и служебная проба — не успех;
                    # прерыватель кормится только РАБОЧИМИ задачами.
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

        if config.enable_self_build:
            self._run_self_build_proposal(config)

        if status == "stopped":
            self._record_incident(stop_reason, tasks)

        self._log("autonomous_runtime_stop", report.to_dict())
        return report

    def _record_incident(
        self,
        stop_reason: str,
        tasks: list[AutonomousTaskReport],
    ) -> None:
        """Open a structured incident when the run is halted by the circuit.

        A circuit-open stop is a real safety event: it must leave a durable,
        human-readable trace instead of just a log line. Best-effort and
        de-duplicated (one open incident per trigger/module at a time) so the
        daemon never spams the log, and never raises back into the run path.
        """
        log = self.incident_log
        if log is None:
            return
        trigger = "autonomous_run_stopped"
        affected = "core.autonomous_runtime"
        try:
            for inc in log.open_incidents():
                if inc.trigger == trigger and inc.affected_module == affected:
                    return  # already tracked — do not open a duplicate
            failed = sum(1 for t in tasks if t.status == "failed")
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
            # This method exists to leave a record that the run halted. Failing
            # it silently produces exactly the state it was written to prevent:
            # a halt with no incident, indistinguishable from a clean stop
            # (MIR-077).
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
            # Not silence: the failure leaves as the task's own report — status
            # `failed`, the exception type in the summary AND in `details`,
            # which `run_task_queue` writes to the queue row and `_log`s as
            # `autonomous_task_end`. Broad on purpose: one task must not take
            # the whole unattended run down with it.
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
        # A timed-out run (or one with no exit code) did NOT actually establish
        # health: passed=0/failed=0 only means "we never finished", not "green".
        # Report it as inconclusive so downstream never reads it as success.
        if timed_out or exit_code is None:
            status: AutonomousTaskStatus = "inconclusive"
            summary = (
                f"inconclusive (timed_out={timed_out}, exit_code={exit_code}); "
                f"passed={passed} failed_or_errors={failed}"
            )
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
                # Какой код проверялся. Инструмент это возвращает (PR #301), а
                # отчёт выбрасывал — и автономный путь, где агент судит о себе
                # сам, оставался без ответа на «упало у меня или в проекте».
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
                # 0 stays, because the caller wants an int — but a 0 that means
                # "counted none" and a 0 that means "could not count" read the
                # same in the run report, and the operator has no way to tell
                # them apart. The event is what separates them (MIR-077).
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
            # Same shape as `_usage_counters`: "the store holds 0 records" and
            # "the store could not be read" are different facts and used to
            # produce the same number in the report (MIR-077).
            self._log(
                "memory_count_failed",
                {"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return 0

    def _gateway_path(self) -> GatewayPath:
        return gateway_path_from_receipt(self.receipt_path)

    def _gate_question_report(self, task: AutonomousTask, answer: str) -> AutonomousTaskReport:
        """Отчёт о ходе, на котором петля ответила вопросом, а не работой."""
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

        Dry-run effect blocking is handled by the actuation gateway
        (``gateway_dry_run`` on the parent loop) — not ``policy.blocked_tools``.

        Posture blocks always apply on this unattended path: ``spawn_subagent``
        and network egress (see ``_AUTONOMOUS_GOAL_BLOCKED_TOOLS``). When tests
        are disabled (include_tests=False), ``run_tests`` is also blocked so the
        planner cannot run tests against operator intent (e.g. :work-session or
        :auto-run --no-tests).

        Gateway and policy blocks are run-scoped and always restored, even on error.
        """
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
        # Prune the planner-visible tool surface so the planner does not even
        # *propose* run-scoped-blocked tools on the unattended goal path. Policy
        # (below) stays as defense-in-depth if a blocked tool is attempted anyway.
        planner = getattr(self.agent, "planner", None)
        planner_supports_hidden = planner is not None and hasattr(planner, "hidden_tools")
        previous_hidden = (
            getattr(planner, "hidden_tools", frozenset())
            if planner_supports_hidden
            else frozenset()
        )
        if planner_supports_hidden:
            planner.hidden_tools = to_block
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
            # NOTE: no task_id yet. By this point the RuntimeTask (which has
            # the id, see run_task_queue) has been reduced to an AutonomousTask
            # carrying only kind+description. Threading the queue's task id
            # across that boundary belongs to the autonomous write-back step,
            # where it is actually consumed.
            #
            # The run's own narrowing lives in the run context, not on the
            # agent: `run_restrictions` unions the block set and ORs dry-run
            # with whatever is already in force, so this task cannot lift a
            # host limit and cannot have its own lifted by a neighbouring run
            # finishing (MIR-114, proofs 6 and 7).
            with run_restrictions(
                blocked_tools=to_block if block else frozenset(),
                dry_run=bool(config.dry_run),
            ):
                # Флаг ворот — свойство ОДНОГО прогона: наследовать его от
                # прошлого хода значило бы красить настоящий ответ в вопрос.
                self.agent.last_answer_was_clarification = False
                answer = self.agent.run(user_question=task.description)
        finally:
            self.agent.gateway_path = previous_gateway_path
            self.agent.gateway_kill_switch = previous_gateway_kill_switch
            self.agent.gateway_budget_snapshot = previous_gateway_budget_snapshot
            self.agent.gateway_readiness_blockers = previous_gateway_readiness_blockers
            self.agent.gateway_check_readiness = previous_gateway_check_readiness
            self.agent.suppress_durable_learning_writes = previous_suppress_learning_writes
            if planner_supports_hidden:
                planner.hidden_tools = previous_hidden
        # Вопрос с ворот петли — не работа (Д1, замер 2026-09-03: «Я не могу
        # безопасно продолжить. Уточни: …» уходил как done → completed/useful).
        # Та же форма отчёта, что у ветки replan_exhausted ниже: статус
        # «clarify», вопрос в details['clarification'].
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
            # A9 (2026-09-03): пустой ответ замечался («(no answer)») и всё
            # равно уезжал как done. Ничего не сделано — «inconclusive».
            return AutonomousTaskReport(task, "inconclusive", "(no answer)", {"answer": answer})
        return AutonomousTaskReport(
            task,
            "done",
            answer[:120].replace("\n", " "),
            {"answer": answer},
        )





    def _active_standing_grant(self):
        """Действующий стоячий грант с остатком на сегодня, или None."""
        return active_standing_grant(
            self.approval_inbox, self.workspace, log=self._log
        )


    def _granted_effects_approval(self, config: AutonomousRuntimeConfig):
        """Одобренное разрешение ДЛЯ ЭТОЙ ЖЕ цели, или None.

        Сверка по тому же ключу, которым заявка заводится: одобрение цели A не
        разрешает цель B. Берётся самое старое подходящее.
        """
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
        """Один ключ на цель — им и заводят заявку, и находят её одобренной."""
        digest = hashlib.sha256(goal.encode("utf-8")).hexdigest()[:16]
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
        # Honour the same operator brake the rest of the agent uses: when
        # AGENT_FREEZE_AUTO_MEMORY froze 'agent-auto' writes, reflection's
        # auto-generated lessons (which are agent-initiated memory growth)
        # must be frozen too — otherwise they slip past the freeze straight
        # into the persistent store. Derived from the live write policy so
        # the runtime never re-reads the environment.
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
            # ── Consume the LearningPlan: ingest the files the reflection engine
            # identified as weak spots so the agent actually studies them.
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
                            # A count of ingested files says nothing about
                            # whether the diagnosed weak spot was the thing
                            # studied. `grounding` carries that distinction so
                            # a substitution cannot read as success (MIR-106).
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


#: Queue kinds the runtime can execute. `resume_checkpoint` is here because the
#: budget guard writes one expressly for the unattended path; leaving it out
#: meant the queue held rows nothing could ever run.
_RUNNABLE_TASK_KINDS: frozenset[str] = frozenset({"auto_run", "resume_checkpoint"})


def _config_from_task(task: RuntimeTask) -> AutonomousRuntimeConfig:
    """Turn a claimed queue row into a run configuration.

    A `resume_checkpoint` runs as a RE-RUN of its goal, not a state-exact
    resume: exact resumption is the human `--resume` path. Why both, and why
    refusing the kind was a dead end: docs/CODE_NOTES.md, "Rows nothing frees".
    """
    if task.kind not in _RUNNABLE_TASK_KINDS:
        raise ValueError(f"unsupported runtime task kind: {task.kind}")
    return AutonomousRuntimeConfig(
        goal=task.goal,
        dry_run=task.dry_run,
        limit=task.limit,
        include_tests=task.include_tests,
        learning_limit=task.learning_limit,
        # MIR-068 (operator's autonomy grant, 2026-08-03): a queued goal must
        # EXECUTE, not be carried and silently dropped — the tick used to
        # report `completed` while only status+learn ran. One wire, two locks
        # untouched: `_build_queue` still refuses a goal task for the default
        # "project health" placeholder, and `_task_goal` still applies the
        # unattended posture blocks, gateway dry-run and the agent-runs budget.
        include_goal=True,
    )
