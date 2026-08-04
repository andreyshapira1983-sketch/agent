"""Цикл попыток: план → исполнение → вердикт → перепланирование.

Правило оператора: «ни один файл кода не длиннее 2000 строк» и «разбирай
большие файлы на компактные подключаемые модули — не дублируя и не искажая».
Десятый кусок раскола `core/loop.py`.

ЭТОТ КУСОК ОТЛИЧАЕТСЯ ОТ ПРЕДЫДУЩИХ ДЕВЯТИ. Те переезжали дословно, символ в
символ. Здесь так не вышло: цикл держится за 22 run-локали `_run_inner`, а
`ruff.toml` этого же репозитория ставит `max-args = 12`. Список параметров на
22 позиции — не решение, а тот же клубок, переложенный в подпись.

Поэтому состояние прогона названо ЯВНО — `AttemptState` ниже. Он не структура
для красоты: это перечень того, что цикл попыток на самом деле носит с собой,
до сих пор существовавший только в виде россыпи локальных имён посреди
двухтысячестрочного метода. Что из этого вход, что рабочее, а что уезжает
наружу — теперь написано, а не выводится чтением.

Перенос при этом остался ПРОВЕРЯЕМЫМ: тело цикла получено из истории одной
объявленной подстановкой `имя -> st.имя` по 22 полям, и
`tests/test_loop_attempt_split.py` применяет ту же подстановку к историческому
коду и требует совпадения AST. Это ровно та же сила, что у дословной сверки в
кусках 1–9, только преобразование названо.

Класс подмешивается в ``AgentLoop``; состояние агента по-прежнему живёт на
композированном цикле, а `AttemptState` — состояние ОДНОГО прогона.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar

from core.assumption_registry import extract_from_plan
from core.evidence import ProvenanceChain, evidence_from_tool_result
from core.file_request_intent import force_file_hint_read_when_explicit
from core.model_usage import ModelBudgetExceeded
from core.models import ErrorObject, Goal, Plan, PlanStep
from core.planner import PlannerOutput
from core.reasoning_action_check import check_reasoning_actions
from core.replan import ReplanTrigger, count_failures, format_replan_context
from core.task_complexity import can_skip_planner


@dataclass
class AttemptState:
    """То, что цикл попыток носит с собой за один прогон.

    Имена полей совпадают с прежними локальными именами `_run_inner` — это
    условие проверяемости переноса: подстановка `имя -> st.имя` механическая,
    и тест сверяет её с историей. Переименовывать поля можно, но тогда
    подстановка в тесте обязана переименоваться вместе с ними.
    """

    # ── Вход: за цикл не меняется ────────────────────────────────────────
    user_question: str
    file_hint: str | None
    goal: Goal
    planner_history: str
    failure_history: list[ReplanTrigger]
    local_critique_active: bool
    forced_sources: list[str] | None
    forced_reasoning: str
    forced_warnings: list[str]
    _task_planner_llm: Any
    _run_assumptions: Any
    _cp: Any

    # ── Рабочее: живёт между попытками ───────────────────────────────────
    #: MVP-12: совет и запреты, доставшиеся от `policy.decide()` прошлой
    #: попытки. Пусто на первой, наполняется на каждом перепланировании.
    advice_for_planner: str = ""
    forbidden_actions: tuple[tuple[str, str], ...] = ()
    attempt: int = 0

    # ── Выход: это читают ПОСЛЕ цикла ────────────────────────────────────
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: MVP-14.1 — типизированная цепочка улик, растёт параллельно артефактам.
    chain: ProvenanceChain = field(default_factory=ProvenanceChain)
    planner_out: PlannerOutput | None = None
    plan: Plan | None = None
    replan_exhausted: bool = False
    #: Дешёвый путь: поднимается только веткой пропуска планировщика на
    #: тривиальном ходе без инструментов. Ниже по коду он урезает контекст
    #: синтезатора, форсирует дешёвый ярус модели и пропускает конвейер знаний.
    cheap_path_active: bool = False
    #: Тень S2: ставится при обнаружении застоя, читается в конце прогона —
    #: «во что обошлась бы ранняя остановка». Ничего не останавливает.
    _stagnation_shadow: dict[str, Any] | None = None

    #: Поля, ради которых вызывающий вообще смотрит в состояние после цикла.
    #: Объявлено, а не оставлено комментарием, потому что молчаливая потеря
    #: выхода — самый тихий способ сломать этот перенос: цикл отработает,
    #: результат ляжет в `st`, и никто его не заберёт. Тест требует, чтобы
    #: `_run_inner` распаковывал РОВНО этот набор, и чтобы каждое имя в нём
    #: цикл действительно писал.
    #:
    #: Остальное делится на вход (за цикл не меняется) и рабочее состояние
    #: между попытками — `advice_for_planner` и `forbidden_actions` приходят
    #: от `policy.decide()` прошлой попытки и наружу не едут.
    OUTPUTS: ClassVar[frozenset[str]] = frozenset({
        "artifacts",
        "chain",
        "planner_out",
        "plan",
        "attempt",
        "replan_exhausted",
        "cheap_path_active",
        "_stagnation_shadow",
    })


class AgentLoopAttempt:
    """Ограниченный цикл перепланирования вокруг плана и его исполнения.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется. Тот же приём, что в ``loop_step_execution``.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        planner: Any
        replan_policy: Any
        _current_attempt: int
        _termination_guard: Any

        # Объявляем ВЫЗЫВАЕМЫМИ атрибутами: заглушка-функция с пустым телом
        # читается анализаторами как «функция без return», и каждый вызов
        # ложно помечается E1111.
        _execute_steps_parallel: Any
        _sensor_failed: Any

        # Берётся у соседней примеси: работает через MRO, но связь между
        # модулями обязана быть записана, иначе её видно только на прогоне.
        _defect_signals: Any
        _episodic_store_mtime: Any
        _executed_tools: Any
        _planner_cache: Any
        cheap_path_enabled: Any
        last_referent_decision: Any

    def _run_attempt_loop(self, st: AttemptState) -> None:
        """Крутить попытки, пока не выйдет ответ или не кончится бюджет.

        Ничего не возвращает: всё, что цикл наработал, лежит в `st` —
        артефакты, цепочка улик, последний план и вердикт о том, исчерпано ли
        перепланирование. Вызывающий читает поля оттуда.
        """
        while True:
            st.attempt += 1
            self._current_attempt = st.attempt

            # Emit a structured `replan_attempt` event for every loop
            # iteration AFTER the first — makes it trivial for tests
            # (and humans) to see how many attempts ran and which advice
            # the planner saw on each.
            if st.attempt > 1:
                self.log.log(
                    "replan_attempt",
                    {
                        "attempt": st.attempt,
                        "max_total": self.replan_policy.max_total_replans,
                        "advice_chars": len(st.advice_for_planner),
                        "forbidden_action_count": len(st.forbidden_actions),
                        "failure_counts_so_far": dict(
                            count_failures(st.failure_history)
                        ),
                    },
                )

            failure_context = format_replan_context(
                st.failure_history,
                st.attempt,
                self.replan_policy.max_total_replans,
                advice=st.advice_for_planner,
                forbidden_actions=st.forbidden_actions,
            )

            if st.attempt == 1 and st.forced_sources is not None:
                st.planner_out = PlannerOutput(
                    reasoning=st.forced_reasoning,
                    sources=st.forced_sources,
                    raw_response="",
                    warnings=st.forced_warnings,
                )
            else:
                # ── Local-critique path (PR2) ─────────────────────────────
                # Resolved referent + critique/show-only → skip planner tools
                # and synthesise from analysis_target (not memory/GK).
                if (
                    st.local_critique_active
                    and st.attempt == 1
                    and not failure_context.strip()
                ):
                    st.planner_out = PlannerOutput(
                        reasoning=(
                            "Local critique path: referent resolved — answering "
                            "from analysis_target without tools or memory/GK."
                        ),
                        sources=[],
                        raw_response="",
                        warnings=["planner_skipped_local_critique"],
                        diagnostics={
                            "stage": "skipped",
                            "reason": "referent_local_critique",
                            "fallback": "local_critique",
                        },
                    )
                    self.log.log(
                        "planner_local_critique",
                        {
                            "question_chars": len(st.user_question),
                            "kind": (
                                None
                                if self.last_referent_decision is None
                                or self.last_referent_decision.primary is None
                                else self.last_referent_decision.primary.kind
                            ),
                        },
                    )
                # ── Cheap-path gate ───────────────────────────────────────
                # Trivial, no-tool input (config-flag echoes, greetings, one
                # line "what is X") never needs a tool: the planner would only
                # spend a full LLM call to return an empty plan. Skip that call
                # and let the normal empty-plan flow synthesise the answer.
                # Gated to the first attempt with no failure/replan context so
                # replans always get the real planner.
                elif (
                    self.cheap_path_enabled
                    and st.attempt == 1
                    and not failure_context.strip()
                    and can_skip_planner(st.user_question, file_hint=st.file_hint)
                ):
                    st.planner_out = PlannerOutput(
                        reasoning=(
                            "Cheap path: trivial no-tool input — answering from "
                            "general knowledge and memory without a planner call."
                        ),
                        sources=[],
                        raw_response="",
                        warnings=["planner_skipped_cheap_path"],
                        diagnostics={
                            "stage": "skipped",
                            "reason": "trivial no-tool input",
                            "fallback": "cheap_path",
                        },
                    )
                    self.log.log(
                        "planner_cheap_path",
                        {
                            "question_chars": len(st.user_question),
                            "reason": "trivial no-tool input",
                        },
                    )
                    st.cheap_path_active = True
                else:
                    # ── Planner cache ─────────────────────────────────────
                    # Cache key: (question hash, episodic store mtime, file_hint).
                    # Mtime invalidates the cache whenever a new episode is written
                    # (the store changes → the planner might choose different tools).
                    # Only applied on the first attempt with no failure context.
                    _pc_key = (
                        hash(st.user_question.lower().strip()),
                        self._episodic_store_mtime(),
                        st.file_hint or "",
                    )
                    if (
                        st.attempt == 1
                        and not failure_context.strip()
                        and _pc_key in self._planner_cache
                    ):
                        st.planner_out = self._planner_cache[_pc_key]
                        self.log.log(
                            "planner_cache_hit",
                            {
                                "key_hash": _pc_key[0],
                                "tools_cached": [s["tool"] for s in st.planner_out.sources],
                            },
                        )
                    else:
                        try:
                            st.planner_out = self.planner.plan(
                                question=st.user_question,
                                file_hint=st.file_hint,
                                history=st.planner_history,
                                failure_context=failure_context,
                                forbidden_actions=st.forbidden_actions,
                                llm=st._task_planner_llm,
                            )
                        except ModelBudgetExceeded as exc:
                            self._save_budget_pause_checkpoint(
                                st._cp,
                                goal=st.goal,
                                question=st.user_question,
                                file_hint=st.file_hint,
                                current_phase="planning",
                                plan=st.plan,
                                blocked=exc,
                            )
                            raise
                        if (
                            st.attempt == 1
                            and not failure_context.strip()
                            and "plan_parse_failed" not in st.planner_out.warnings
                        ):
                            self._planner_cache[_pc_key] = st.planner_out
                st.planner_out = force_file_hint_read_when_explicit(
                    st.planner_out,
                    question=st.user_question,
                    file_hint=st.file_hint,
                )
            self.log.log(
                "planner",
                {
                    "reasoning": st.planner_out.reasoning,
                    "tools_chosen": [s["tool"] for s in st.planner_out.sources],
                    "warnings": st.planner_out.warnings,
                    "raw_chars": len(st.planner_out.raw_response),
                    "attempt": st.attempt,
                    "replan_context_chars": len(failure_context),
                },
            )
            # Fix #2: surface planner hallucinations as a dedicated event.
            # When the LLM invents a tool name that is not in the registry,
            # _validate_steps drops the step silently.  If every step was
            # dropped (plan_empty_after_drop=True) the loop will proceed to
            # synthesise from general knowledge — making the answer look
            # confident when the actual plan failed.  This event lets
            # operators detect the failure mode without parsing warning strings.
            if st.planner_out.dropped_tools:
                self.log.log(
                    "plan_tool_drop",
                    {
                        "dropped": st.planner_out.dropped_tools,
                        "plan_empty_after_drop": not st.planner_out.sources,
                        "attempt": st.attempt,
                    },
                )

            # MAST FM-2.6 — reasoning ↔ action consistency check.
            try:
                _ra_report = check_reasoning_actions(
                    st.planner_out.reasoning,
                    [s["tool"] for s in st.planner_out.sources],
                )
                if _ra_report.has_mismatch:
                    self.log.log(
                        "reasoning_action_mismatch",
                        {
                            **_ra_report.to_log_payload(),
                            "attempt": st.attempt,
                        },
                    )
                    # Banked with the episode as well as logged. Still decides
                    # nothing — S4's ruling keeps this an observer — but the
                    # journal is per-run and disappears from the agent's own
                    # memory, which is where a repeated fault has to be visible.
                    self._defect_signals.append("reasoning_action_mismatch")
            except Exception:
                pass  # Observational only — must never abort the loop.

            st.plan = self._build_plan(st.goal, st.planner_out.sources)
            self.log.log("plan", st.plan, steps=len(st.plan.steps), attempt=st.attempt)
            st._cp.save_plan(attempt=st.attempt, step_ids=[s.id for s in st.plan.steps])

            # Layer 5 — extract plan-level assumptions on the first attempt only.
            if st.attempt == 1:
                try:
                    _plan_assumptions = extract_from_plan(
                        st.planner_out.sources,
                        question=st.user_question,
                        run_id=getattr(self.log, "trace_id", ""),
                    )
                    st._run_assumptions.register_many(_plan_assumptions)
                    if st._run_assumptions.assumptions:
                        self.log.log(
                            "assumptions_registered",
                            {
                                "count": len(st._run_assumptions),
                                "assumptions": st._run_assumptions.to_log_payload(),
                            },
                        )
                except Exception:
                    pass  # Never abort the run.

            attempt_artifacts: dict[str, dict[str, Any]] = {}
            attempt_failures: list[ReplanTrigger] = []
            attempt_chain = ProvenanceChain()

            # Planner JSON parse failure: empty `sources` here is NOT an
            # intentional general-knowledge plan, it's a contract break.
            # Without this gate the loop would fall through to the
            # `if not plan.steps or attempt_artifacts: break` branch
            # below and the synthesizer would happily produce a long
            # confident answer from zero evidence. Treat it as a real
            # failure so `replan_policy.decide()` either gets us a clean
            # JSON retry or trips `replan_exhausted` and the synthesizer
            # writes the honest "I could not plan" reply.
            plan_parse_failed = (
                "plan_parse_failed" in (st.planner_out.warnings or ())
            )
            if plan_parse_failed:
                _parse_diag = dict(getattr(st.planner_out, "diagnostics", {}) or {})
                self.log.log(
                    "plan_parse_failed",
                    {
                        "attempt": st.attempt,
                        "warnings": list(st.planner_out.warnings),
                        "raw_chars": len(st.planner_out.raw_response),
                        # Sanitised, length-capped preview (no full secrets).
                        "raw_preview": _parse_diag.get("raw_preview")
                        or st.planner_out.raw_response[:240],
                        "diagnostics": _parse_diag,
                    },
                )
                attempt_failures.append(
                    ReplanTrigger(
                        code="plan_parse_failed",
                        step_id="planner",
                        tool_name=None,
                        arguments={},
                        reason=(
                            "Planner LLM reply did not parse as JSON "
                            f"(raw_chars={len(st.planner_out.raw_response)})."
                        ),
                        attempt=st.attempt,
                    )
                )

            for step, outcome, trigger in self._execute_steps_parallel(st.plan.steps):
                if outcome is None:
                    step.status = "failed"
                    if trigger is not None:
                        attempt_failures.append(trigger)
                    continue
                self._executed_tools.append(outcome["tool"])
                attempt_artifacts[outcome["label"]] = {
                    "tool": outcome["tool"],
                    "output": outcome["output"],
                    "issues": outcome["issues"],
                }
                # MVP-14.1 — typed evidence. The output is already
                # redacted (see _execute_step), so the excerpt that
                # ends up on disk in the chain is safe.
                ev = evidence_from_tool_result(
                    tool_name=outcome["tool"],
                    arguments=outcome.get("arguments"),
                    output=outcome["output"],
                    status="success",
                )
                if ev is not None:
                    attempt_chain.add(ev)
                step.status = "done"
                st._cp.save_act(
                    label=outcome["label"],
                    tool=outcome["tool"],
                    chars=len(str(outcome["output"])),
                    status="done",
                )

            # Success: either a 0-step plan (general-knowledge / history-only
            # answer is intentional) or at least one artifact came through.
            # `plan_parse_failed` is NOT success — empty `sources` came from
            # a JSON parse failure, not from the planner choosing zero tools.
            if (not st.plan.steps and not plan_parse_failed) or attempt_artifacts:
                st.artifacts = attempt_artifacts
                st.chain = attempt_chain
                break

            # Failure: this attempt produced nothing usable. Carry the
            # triggers forward and ask the policy what to do next.
            st.failure_history.extend(attempt_failures)

            # MAST FM-1.5 — stagnation check: same failure signature twice
            # in a row means the loop is looping. Observational only.
            try:
                _stag = self._termination_guard.observe_attempt(
                    attempt=st.attempt,
                    failure_codes=[t.code for t in attempt_failures],
                    artifact_labels=list(attempt_artifacts.keys()),
                )
                if _stag is not None:
                    self.log.log("stagnation_detected", _stag.to_log_payload())
                    # Shadow accounting (operator ruling 2026-07-27): record
                    # WHERE a stop would have happened, so the run can report at
                    # the end what stopping would have cost or saved. Nothing is
                    # stopped.
                    st._stagnation_shadow = {
                        "attempt": st.attempt,
                        "artifacts_at_detection": sorted(attempt_artifacts.keys()),
                        "repeat_count": _stag.repeat_count,
                        "failure_codes": list(_stag.failure_codes),
                    }
            except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
                self._sensor_failed("stagnation_shadow", exc)

            decision = self.replan_policy.decide(
                failure_history=st.failure_history,
                completed_attempts=st.attempt,
            )

            if decision.action == "continue":
                # Log `replan` ONLY when we are going to try again.
                # Pairs neatly with `replan_attempt` (next iteration).
                self.log.log(
                    "replan",
                    {
                        "attempt": st.attempt,
                        "next_attempt": st.attempt + 1,
                        "max_total": self.replan_policy.max_total_replans,
                        "triggers": [t.code for t in attempt_failures],
                        "details": [
                            {
                                "step_id": t.step_id,
                                "tool": t.tool_name,
                                "code": t.code,
                                "reason": t.reason,
                            }
                            for t in attempt_failures
                        ],
                        "decision": decision.to_log_payload(),
                    },
                )
                st.advice_for_planner = decision.advice_for_planner
                st.forbidden_actions = decision.forbidden_actions
                continue

            # Policy said stop. Emit a structured exhaustion event AND
            # an `error` event (kept for backward-compat with existing
            # consumers that grep on `code=replan_exhausted`). The
            # synthesizer takes over with an honest explanation.
            st.replan_exhausted = True
            err = ErrorObject(
                source="loop",
                code="replan_exhausted",
                message=(
                    f"Re-planning stopped after {st.attempt} attempt(s): "
                    f"{decision.reason}. Failure codes: "
                    f"{[t.code for t in st.failure_history]}"
                ),
                severity="error",
                recoverable=False,
                context={
                    "attempts": st.attempt,
                    "max_total": self.replan_policy.max_total_replans,
                    "decision_action": decision.action,
                    "decision_reason": decision.reason,
                    "failure_counts": dict(decision.failure_counts),
                    "failure_codes": [t.code for t in st.failure_history],
                },
            )
            self.log.log("error", err)
            self.log.log(
                "replan_exhausted",
                {
                    "attempts": st.attempt,
                    "max_total": self.replan_policy.max_total_replans,
                    "decision_action": decision.action,
                    "decision_reason": decision.reason,
                    "failure_counts": dict(decision.failure_counts),
                    "triggers": [t.code for t in attempt_failures],
                },
            )
            break

    def _build_plan(self, goal: Goal, sources: list[dict[str, Any]]) -> Plan:
        plan = Plan(goal_id=goal.id)
        for i, src in enumerate(sources, start=1):
            plan.steps.append(  # pylint: disable=no-member  # pydantic list field, real list at runtime
                PlanStep(
                    plan_id=plan.id,
                    order=i,
                    action_spec={
                        "type": "tool_call",
                        "tool_name": src["tool"],
                        "arguments": src["arguments"],
                        "source_label": src["label"],
                    },
                    expected_outcome=src["expected_outcome"],
                )
            )
        plan.status = "in_progress"
        return plan

    @staticmethod
    def _checkpoint_step_summaries(plan: Plan | None) -> list[dict[str, Any]]:
        if plan is None:
            return []
        summaries: list[dict[str, Any]] = []
        for step in plan.steps:
            action = step.action_spec or {}
            summaries.append(
                {
                    "id": step.id,
                    "order": step.order,
                    "tool": action.get("tool_name") or action.get("tool"),
                    "source_label": action.get("source_label"),
                    "status": step.status,
                    "expected_outcome": step.expected_outcome,
                }
            )
        return summaries

    def _save_budget_pause_checkpoint(
        self,
        checkpoint: Any,
        *,
        goal: Goal,
        question: str,
        file_hint: str | None,
        current_phase: str,
        plan: Plan | None,
        blocked: ModelBudgetExceeded,
    ) -> None:
        save_paused = getattr(checkpoint, "save_paused", None)
        if not callable(save_paused):
            return
        planned_steps = self._checkpoint_step_summaries(plan)
        completed_steps = [
            step for step in planned_steps if step.get("status") == "done"
        ]
        remaining_steps = [
            step for step in planned_steps if step.get("status") != "done"
        ]
        payload = {
            "active_goal": goal.description,
            "goal_id": goal.id,
            "original_user_question": question,
            "file_hint": file_hint,
            "current_phase": current_phase,
            "planned_steps": planned_steps,
            "completed_steps": completed_steps,
            "remaining_steps": remaining_steps,
            "stop_reason": "budget_exhausted",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "blocked_model": blocked.to_dict(),
        }
        try:
            save_paused(payload)
            self.log.log(
                "resumable_checkpoint_paused",
                {
                    "current_phase": current_phase,
                    "stop_reason": "budget_exhausted",
                    "planned_steps": len(planned_steps),
                    "completed_steps": len(completed_steps),
                    "remaining_steps": len(remaining_steps),
                    "blocked_model": payload["blocked_model"],
                },
            )
        except Exception as exc:  # наблюдательный сенсор: сбой журналируется, ход не ломается
            self._sensor_failed("budget_exhaustion_log", exc)
