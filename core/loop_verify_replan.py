"""Проверка ответа и перепланирование по неразрешённым цитатам.

Правило оператора: «ни один файл кода не длиннее 2000 строк» и «разбирай
большие файлы на компактные подключаемые модули — не дублируя и не искажая».
Одиннадцатый кусок раскола `core/loop.py` — и второй, который переезжает не
дословно, а под объявленной подстановкой (первым был цикл попыток).

Суть участка: черновик проверяется, и если модель сослалась на URL, которого
в цепочке улик нет (вердикт `cited_but_unmatched`), это считается структурным
сбоем `unresolved_citation` и подаётся в ТУ ЖЕ `ReplanPolicy`, что управляет
сбоями инструментов. Планировщику прямо говорят, какие адреса добыть; после
`web_fetch` исходный черновик перепроверяется на обогащённой цепочке — второй
синтез не нужен, потому что черновик уже цитирует эти адреса.

Почему состояние, а не параметры: участок держится за 21 run-локаль, потому
что перепланирование перезапускает попытку целиком — со своим планом,
исполнением и каталогизацией. `ruff.toml` этого же репозитория ставит
`max-args = 12`; список на 21 позицию был бы тем же клубком в подписи.

Класс подмешивается в ``AgentLoop``; `VerifyState` — состояние ОДНОГО прогона.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from core.evidence import ProvenanceChain, evidence_from_tool_result
from core.file_request_intent import force_file_hint_read_when_explicit
from core.model_usage import ModelBudgetExceeded
from core.models import Goal, Plan
from core.planner import PlannerOutput
from core.replan import ReplanTrigger, count_failures, format_replan_context
from core.source_ranker import SourceRankingReport, rank_chain


@dataclass
class VerifyState:
    """То, что проверка и перепланирование по цитатам носят с собой.

    Имена полей совпадают с прежними локальными именами `_run_inner` — это
    условие проверяемости переноса: подстановка `имя -> st.имя` механическая,
    и тест сверяет её с историей.
    """

    # ── Вход: за участок не меняется ─────────────────────────────────────
    draft_answer: str
    user_question: str
    file_hint: str | None
    goal: Goal
    chain: ProvenanceChain
    artifacts: dict[str, dict[str, Any]]
    attempt: int
    plan: Plan | None
    planner_history: str
    failure_history: list[ReplanTrigger]
    source_ranking: SourceRankingReport
    source_registry: Any
    may_knowledge: bool
    may_source_registry: bool
    _task_planner_llm: Any
    _disagreement_shadow: list[dict[str, Any]]
    _cp: Any

    # ── Выход: это читают ПОСЛЕ участка ──────────────────────────────────
    #: Текст, доехавший до решателей черновика. При выключенном верификаторе
    #: это черновик как есть, иначе — размеченный отчётом.
    answer: str = ""
    planner_out: PlannerOutput | None = None
    replan_exhausted: bool = False
    #: НЕ «не подтвердилось», а «проверка сломалась». Ниже по цепочке
    #: решателей эти два случая обязаны различаться.
    verifier_failure: bool = False

    #: Ради чего вызывающий смотрит в состояние после участка. Объявлено, а
    #: не оставлено комментарием: молчаливая потеря выхода — самый тихий
    #: способ сломать такой перенос, и тест держит распаковку по этому списку.
    OUTPUTS: ClassVar[frozenset[str]] = frozenset({
        "answer", "planner_out", "replan_exhausted", "verifier_failure",
    })


class AgentLoopVerifyReplan:
    """Проверка ответа и перепланирование по неразрешённым цитатам.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется. Тот же приём, что в ``loop_step_execution``.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        planner: Any
        replan_policy: Any
        verifier_enabled: Any
        knowledge_pipeline: Any
        knowledge_auto_write: Any
        source_registry_store: Any
        last_verification: Any
        last_provenance: Any
        last_source_registry: Any
        last_knowledge_pipeline: Any
        _current_attempt: int

        # Объявляем ВЫЗЫВАЕМЫМИ атрибутами: заглушка-функция с пустым телом
        # читается анализаторами как «функция без return», и каждый вызов
        # ложно помечается E1111.
        _verify_draft: Any
        _build_plan: Any
        _knowledge_remember_batch: Any
        _quarantine_conflicted_memory: Any

        # Берётся у соседней примеси: работает через MRO, но связь между
        # модулями обязана быть записана, иначе её видно только на прогоне.
        _execute_steps_parallel: Any
        _executed_tools: Any
        _save_budget_pause_checkpoint: Any
        _verification_receipt_kwargs: Any

    def _replan_on_refuted_claims(self, report: Any, st: VerifyState) -> None:
        """Rung 4 of the ladder — carry the WHY into the next attempt.

        A refuted claim knows `expected`, `actual` and the numbers behind them.
        Unless that reaches the next attempt it is a correct diagnosis in a
        chart the patient never reads, which is the failure mode the operator
        named when direction (b) was chosen.

        It travels the road `unresolved_citation` already uses: a
        `ReplanTrigger` whose `reason` is copied into `<replan_context>`
        verbatim, and a budget carrying `requires_different_action`, so the
        sanitiser REFUSES a repeat rather than merely advising against one —
        the `web_empty` lesson, where prose advice was never enforcement.

        One trigger for the whole draft, not one per claim: the next attempt
        needs the corrections, not a flood of them.
        """
        refuted = [c for c in report.chunks if getattr(c, "reason", None)]
        if not refuted:
            return
        corrections = "; ".join(
            f"{c.reason.explanation} (from {c.reason.computed_from})"
            for c in refuted[:5]
        )
        st.failure_history.append(ReplanTrigger(
            code="claim_refuted",
            step_id=f"verify-claims-{st.attempt}",
            tool_name=None,
            arguments={"codes": sorted({c.reason.code for c in refuted})},
            reason=(
                f"{len(refuted)} claim(s) were checked against the source they "
                f"cited and do not follow from it: {corrections}"
            ),
            attempt=st.attempt,
        ))
        self.log.log("claims_refuted_by_arithmetic", {
            "count": len(refuted),
            "reasons": [c.reason.to_log_payload() for c in refuted[:5]],
        })

    def _verify_and_settle_answer(self, st: VerifyState) -> None:
        """Довести черновик до текста, который увидят решатели.

        Ничего не возвращает: результат лежит в `st` — сам текст, последний
        вывод планировщика, признак исчерпанного перепланирования и флаг
        поломки верификатора.
        """
        if self.verifier_enabled:
            from core.verifier import (
                extract_unresolved_web_urls,
            )
            from core.verifier import (
                verify as _verify,
            )

            # Кусок 5 разбора `_run_inner`: проверка и сенсоры вокруг неё
            # живут в `core/loop_verification.py`. Второй элемент — «проверка
            # сломалась», а не «не подтвердилось»; ниже они не смешиваются.
            report, st.verifier_failure = self._verify_draft(
                st.draft_answer,
                chain=st.chain,
                user_question=st.user_question,
                attempt=st.attempt,
                plan=st.plan,
                artifacts=st.artifacts,
                failure_history=st.failure_history,
                _disagreement_shadow=st._disagreement_shadow,
            )

            self._replan_on_refuted_claims(report, st)

            verify_replan_attempt = 0
            VERIFY_REPLAN_HARD_CAP = 2  # belt + braces over ReplanPolicy

            while True:
                unresolved_urls = extract_unresolved_web_urls(report)
                if not unresolved_urls:
                    break
                if verify_replan_attempt >= VERIFY_REPLAN_HARD_CAP:
                    self.log.log(
                        "verify_replan_capped",
                        {
                            "attempts": verify_replan_attempt,
                            "hard_cap": VERIFY_REPLAN_HARD_CAP,
                            "unresolved_count": len(unresolved_urls),
                            "unresolved_sample": unresolved_urls[:3],
                        },
                    )
                    break

                verify_replan_attempt += 1
                trigger = ReplanTrigger(
                    code="unresolved_citation",
                    step_id=f"verify-{verify_replan_attempt}",
                    tool_name=None,
                    arguments={"urls": list(unresolved_urls)},
                    reason=(
                        f"Verifier found {len(unresolved_urls)} [web:...] "
                        f"citation(s) the chain cannot resolve. "
                        f"Planner must add web_fetch for: {unresolved_urls}"
                    ),
                    attempt=st.attempt + verify_replan_attempt,
                )
                st.failure_history.append(trigger)

                decision = self.replan_policy.decide(
                    failure_history=st.failure_history,
                    completed_attempts=st.attempt + verify_replan_attempt,
                )

                if decision.action != "continue":
                    st.replan_exhausted = True
                    self.log.log(
                        "replan_exhausted",
                        {
                            "phase": "verify",
                            "attempts": st.attempt + verify_replan_attempt,
                            "max_total": self.replan_policy.max_total_replans,
                            "decision_action": decision.action,
                            "decision_reason": decision.reason,
                            "failure_counts": dict(decision.failure_counts),
                            "triggers": ["unresolved_citation"],
                        },
                    )
                    break

                # Build the planner advice. We surface `unresolved_citation`
                # advice FIRST (the new failure dominates) and then append
                # an explicit URL list the planner must convert into
                # web_fetch steps. The base `decision.advice_for_planner`
                # is composed over all FailureTypes seen so far, in
                # FailureType-declaration order, so it might bury the
                # critical fetch instruction — we prepend ours explicitly.
                base_advice = (
                    self.replan_policy.budgets["unresolved_citation"].advice
                )
                urls_block = "\n".join(f"  - {u}" for u in unresolved_urls)
                verify_advice = (
                    f"{base_advice}\n\n"
                    f"URLs that MUST be opened via web_fetch (one step each):"
                    f"\n{urls_block}"
                )

                self.log.log(
                    "replan",
                    {
                        "phase": "verify",
                        "attempt": st.attempt + verify_replan_attempt - 1,
                        "next_attempt": st.attempt + verify_replan_attempt,
                        "max_total": self.replan_policy.max_total_replans,
                        "triggers": ["unresolved_citation"],
                        "details": [
                            {
                                "step_id": trigger.step_id,
                                "tool": None,
                                "code": "unresolved_citation",
                                "reason": trigger.reason,
                            },
                        ],
                        "decision": decision.to_log_payload(),
                        "unresolved_urls": list(unresolved_urls),
                    },
                )

                # Bump the public attempt counter so `respond.attempts_used`
                # reflects the verify-driven re-plans honestly.
                self._current_attempt = st.attempt + verify_replan_attempt

                self.log.log(
                    "replan_attempt",
                    {
                        "phase": "verify",
                        "attempt": st.attempt + verify_replan_attempt,
                        "max_total": self.replan_policy.max_total_replans,
                        "advice_chars": len(verify_advice),
                        "forbidden_action_count": len(decision.forbidden_actions),
                        "failure_counts_so_far": dict(
                            count_failures(st.failure_history)
                        ),
                    },
                )

                failure_context = format_replan_context(
                    st.failure_history,
                    st.attempt + verify_replan_attempt,
                    self.replan_policy.max_total_replans,
                    advice=verify_advice,
                    forbidden_actions=decision.forbidden_actions,
                )

                try:
                    st.planner_out = self.planner.plan(
                        question=st.user_question,
                        file_hint=st.file_hint,
                        history=st.planner_history,
                        failure_context=failure_context,
                        forbidden_actions=decision.forbidden_actions,
                        # Keep the complexity-escalated planner model on verify
                        # re-plans; without this the re-plan silently dropped to
                        # the default tier a "deep" question was escalated away
                        # from.
                        llm=st._task_planner_llm,
                    )
                except ModelBudgetExceeded as exc:
                    self._save_budget_pause_checkpoint(
                        st._cp,
                        goal=st.goal,
                        question=st.user_question,
                        file_hint=st.file_hint,
                        current_phase="verification_replan",
                        plan=st.plan,
                        blocked=exc,
                    )
                    raise
                st.planner_out = force_file_hint_read_when_explicit(
                    st.planner_out,
                    question=st.user_question,
                    file_hint=st.file_hint,
                )
                self.log.log(
                    "planner",
                    {
                        "phase": "verify",
                        "reasoning": st.planner_out.reasoning,
                        "tools_chosen": [s["tool"] for s in st.planner_out.sources],
                        "warnings": st.planner_out.warnings,
                        "raw_chars": len(st.planner_out.raw_response),
                        "attempt": st.attempt + verify_replan_attempt,
                        "replan_context_chars": len(failure_context),
                    },
                )

                verify_plan = self._build_plan(st.goal, st.planner_out.sources)
                self.log.log(
                    "plan",
                    verify_plan,
                    steps=len(verify_plan.steps),
                    attempt=st.attempt + verify_replan_attempt,
                    phase="verify",
                )

                # Execute the new steps. We tolerate sanitiser/policy drops
                # and per-step failures — if NOTHING gets fetched we'll
                # just exit on the next loop iteration when re-verify
                # still finds the same unresolved URLs. No infinite loop
                # because the hard cap + per-type budget both bound us.
                added_evidence = 0
                for step, outcome, trigger in self._execute_steps_parallel(verify_plan.steps):
                    if outcome is None:
                        step.status = "failed"
                        # Drain the scratch trigger so it doesn't leak into
                        # the next decide() iteration with a misleading code
                        # (e.g. tool_error for a fetch that was sanitised).
                        if trigger is not None:
                            st.failure_history.append(trigger)
                        continue
                    self._executed_tools.append(outcome["tool"])
                    st.artifacts[outcome["label"]] = {
                        "tool": outcome["tool"],
                        "output": outcome["output"],
                        "issues": outcome["issues"],
                    }
                    ev = evidence_from_tool_result(
                        tool_name=outcome["tool"],
                        arguments=outcome.get("arguments"),
                        output=outcome["output"],
                        status="success",
                    )
                    if ev is not None:
                        st.chain.add(ev)
                        added_evidence += 1
                    step.status = "done"

                # Re-verify the ORIGINAL draft against the enriched chain.
                # The draft already cites these URLs (that's why they
                # were unresolved); now the chain has the web_page
                # evidence so `match_citation` will resolve them.
                try:
                    report = _verify(
                        answer=st.draft_answer,
                        chain=st.chain,
                        user_question=st.user_question,
                        expects_contract_headers=getattr(
                            self, "_synthesis_expects_contract_headers", True
                        ),
                        **self._verification_receipt_kwargs(),
                    )
                except Exception as _ver_exc:
                    st.verifier_failure = True
                    self.log.log(
                        "verifier_failure",
                        {
                            "error_type": type(_ver_exc).__name__,
                            "error": str(_ver_exc)[:300],
                            "draft_chars": len(st.draft_answer),
                            "phase": "verify_replan",
                            "iteration": verify_replan_attempt,
                        },
                    )
                    break
                self.log.log(
                    "verification",
                    {
                        **report.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                        "evidence_added": added_evidence,
                    },
                )
                self.last_verification = report
                self.last_provenance = st.chain

                # Re-emit the chain snapshot so callers / log consumers
                # see the enriched provenance after each fetch round.
                self.log.log(
                    "evidence_collected",
                    {
                        "phase": "verify",
                        "count": len(st.chain),
                        "kinds": sorted({ev.kind for ev in st.chain.evidences}),
                        "chain": st.chain.to_log_payload(),
                    },
                )
                st.source_ranking = rank_chain(st.chain, question=st.user_question)
                self.last_source_ranking = st.source_ranking
                self.log.log(
                    "source_ranking",
                    {
                        **st.source_ranking.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                    },
                )
                knowledge_result = self.knowledge_pipeline.run(
                    st.chain,
                    ranking=st.source_ranking,
                    source_store=(
                        self.source_registry_store if st.may_source_registry else None
                    ),
                    remember=(
                        self._knowledge_remember_batch()
                        if st.may_knowledge
                        else None
                    ),
                    auto_write_memory=(
                        self.knowledge_auto_write if st.may_knowledge else False
                    ),
                )
                st.source_registry = knowledge_result.registry
                self._quarantine_conflicted_memory(knowledge_result)
                self.last_source_registry = st.source_registry
                self.log.log(
                    "source_registry",
                    {
                        **st.source_registry.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                    },
                )
                self.last_knowledge_pipeline = knowledge_result
                self.log.log(
                    "knowledge_pipeline",
                    {
                        **knowledge_result.to_log_payload(),
                        "phase": "verify",
                        "iteration": verify_replan_attempt,
                    },
                )

                # If the fetch round added zero evidence, re-verify will
                # produce the same unresolved list — exit instead of
                # looping back to a planner call that has nothing new
                # to work with.
                if added_evidence == 0:
                    self.log.log(
                        "verify_replan_noop",
                        {
                            "iteration": verify_replan_attempt,
                            "unresolved_count_before": len(unresolved_urls),
                            "unresolved_count_after": len(
                                extract_unresolved_web_urls(report)
                            ),
                        },
                    )
                    break

            st.answer = report.annotated_answer
        else:
            st.answer = st.draft_answer
            self.last_verification = None
