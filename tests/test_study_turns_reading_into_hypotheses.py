"""Учебное действие: чтение внешнего мира оставляет ГИПОТЕЗУ, не истину.

Background: docs/CODE_NOTES.md, "Reading leaves a hypothesis".
"""
from __future__ import annotations

from types import SimpleNamespace

from core.best_next_action import select_best_next_action
from core.campaign_io import _propose_hypothesis_from_study
from core.causal_claim_store import distilled_lessons, load_claims
from core.causal_lesson import state_of

_STUDY_GOAL = (
    "Изучи в интернете, как устроен фреймворк LangGraph, и примерь его идеи "
    "к своей системе"
)
_OPEN_ISSUE = {
    "fingerprint": "abc123", "status": "open",
    "action": "improve_failure_to_idea_pipeline",
    "title": "Resolve the open self-improvement issue", "evidence": ["e1"],
}


def test_a_study_goal_beats_the_repair_habit():
    action = select_best_next_action(
        goal=_STUDY_GOAL,
        open_self_improvement_issues=(_OPEN_ISSUE,),
        self_improvement_registry_available=True,
    )

    assert action.action == "study_external_source"


def test_a_repair_goal_is_untouched():
    action = select_best_next_action(
        goal="Найди в своём собственном коде конкретный дефект и почини",
        open_self_improvement_issues=(_OPEN_ISSUE,),
        self_improvement_registry_available=True,
    )

    assert action.action == "improve_failure_to_idea_pipeline"


def test_a_doc_goal_still_outranks_study():
    """Документная цель (58) выше учебной (57): просьба написать — конкретнее
    просьбы почитать.
    """
    action = select_best_next_action(
        goal="Изучи в интернете лучшие практики и напиши черновик 'X.md'",
    )

    assert action.action == "draft_doctrine_document"


class _CondenserLLM:
    def complete(self, **_kw) -> str:
        return (
            "ГИПОТЕЗА: Контрольные точки LangGraph могут улучшить core/loop_attempt\n"
            "ПРОВЕРКА: замер доли прерванных прогонов до и после\n"
            "ИСТОЧНИК: LangGraph Docs Page 7\n"
            "СТАТУС: не проверено"
        )


_ANSWER = (
    "LangGraph строит агентов как графы с контрольными точками "
    "[web_fetch:https://langchain-ai.github.io/langgraph/] и это частично "
    "применимо [web:langgraph обзор]."
)


def test_a_hypothesis_is_recorded_at_the_bottom_rung(tmp_path):
    note = _propose_hypothesis_from_study(
        agent=SimpleNamespace(llm=_CondenserLLM(), log=None),
        workspace=tmp_path, goal=_STUDY_GOAL, answer=_ANSWER,
    )

    assert note.startswith("hypothesis_recorded:")
    ((claim, _extra),) = load_claims(tmp_path)
    assert state_of(claim) == "OBSERVED"
    assert "core/loop_attempt" in claim.observation.observed_mismatch
    assert distilled_lessons(tmp_path) == (), "гипотеза — не урок; предохранитель"


def test_sources_come_from_verified_citations_not_from_the_model(tmp_path):
    """Живой урок конденсатора (2026-08-16): модель выдумала «Page 12».
    Ссылки гипотезы берутся из инлайн-цитат ответа, проверенных верификатором,
    а «ИСТОЧНИК» модели — только справка.
    """
    _propose_hypothesis_from_study(
        agent=SimpleNamespace(llm=_CondenserLLM(), log=None),
        workspace=tmp_path, goal=_STUDY_GOAL, answer=_ANSWER,
    )

    ((claim, _extra),) = load_claims(tmp_path)
    refs = " ".join(claim.observation.evidence_refs)
    assert "langchain-ai.github.io" in refs
    assert "Page 7" not in refs


def test_an_answer_without_web_citations_is_declined(tmp_path):
    note = _propose_hypothesis_from_study(
        agent=SimpleNamespace(llm=_CondenserLLM(), log=None),
        workspace=tmp_path, goal=_STUDY_GOAL,
        answer="Рассуждение без единой веб-цитаты.",
    )

    assert note == "hypothesis_declined:no_web_citations"
    assert load_claims(tmp_path) == ()


def test_a_condenser_without_a_block_is_declined(tmp_path):
    class _Empty:
        def complete(self, **_kw) -> str:
            return "не могу"

    note = _propose_hypothesis_from_study(
        agent=SimpleNamespace(llm=_Empty(), log=None),
        workspace=tmp_path, goal=_STUDY_GOAL, answer=_ANSWER,
    )

    assert note == "hypothesis_declined:no_block"
    assert load_claims(tmp_path) == ()


def test_study_unblocks_only_the_web_on_the_goal_path():
    """Разблокировка узкая по построению: учебному прогону открывается ровно
    web_search/web_fetch; spawn_subagent и python_probe не разблокируемы
    этим полем вовсе.
    """
    from core.autonomous_runtime import _AUTONOMOUS_GOAL_BLOCKED_TOOLS, _goal_block_set

    blocked = _goal_block_set(
        unblock_tools=frozenset({"web_search", "web_fetch", "spawn_subagent",
                                 "python_probe"}),
        include_tests=True,
    )

    assert "web_search" not in blocked
    assert "web_fetch" not in blocked
    assert "spawn_subagent" in blocked
    assert "python_probe" in blocked
    assert _goal_block_set(unblock_tools=frozenset(), include_tests=True) == \
        _AUTONOMOUS_GOAL_BLOCKED_TOOLS
