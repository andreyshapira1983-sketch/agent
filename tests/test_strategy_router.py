"""Tests for core/strategy_router.py — Strategy Router (deliberation kernel layer).

Acceptance criteria from the roadmap
--------------------------------------
1. "Начни безопасную проверку себя"  → safe_self_check   (local, no LLM)
2. "Проверь свои возможности"         → capability_check  (local, no LLM)
3. "Посмотри, что у тебя сейчас не готово" → current_gaps_check (local)
4. "Найди слабое место в своей системе"   → weakness_finder   (local)
5. "Расскажи по README, что ты умеешь"   → general_question  (LLM needed)
6. "Проверь проект и скажи что требует внимания" → project_health (local)

All local strategies must NOT equal general_question.
classify_operator_strategy must never raise.
"""
from __future__ import annotations

import pytest

from core.strategy_router import (
    LOCAL_STRATEGIES,
    OperatorStrategy,
    classify_operator_strategy,
    is_local_strategy,
)


# ---------------------------------------------------------------------------
# Acceptance tests — Russian natural phrases from the roadmap
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase,expected_strategy", [
    # safe_self_check
    ("Начни безопасную проверку себя",       OperatorStrategy.safe_self_check),
    ("Проверь себя безопасно",               OperatorStrategy.safe_self_check),
    ("Проведи безопасную самопроверку",      OperatorStrategy.safe_self_check),
    ("Сделай самопроверку",                  OperatorStrategy.safe_self_check),
    # capability_check
    ("Проверь свои возможности",             OperatorStrategy.capability_check),
    ("Что ты можешь делать",                 OperatorStrategy.capability_check),
    ("Какие у тебя возможности",             OperatorStrategy.capability_check),
    # capability_request
    ("Хочу, чтобы ты сам сообщал мне, когда нужно решение", OperatorStrategy.capability_request),
    ("Нужно подключить email triage",         OperatorStrategy.capability_request),
    # current_gaps_check
    ("Посмотри, что у тебя сейчас не готово",OperatorStrategy.current_gaps_check),
    # weakness_finder
    ("Найди слабое место в своей системе",   OperatorStrategy.weakness_finder),
    # project_health
    ("Проверь проект и скажи что требует внимания", OperatorStrategy.project_health),
    # autonomy_readiness
    ("Насколько ты готов к автономной работе",      OperatorStrategy.autonomy_readiness),
    # budget_status
    ("Сколько токенов я потратил",           OperatorStrategy.budget_status),
    # model_status
    ("Покажи какие модели сейчас используются", OperatorStrategy.model_status),
    # next_safe_test
    ("Скажи, какой безопасный тест сделать следующим", OperatorStrategy.next_safe_test),
    # implementation_plan
    ("Составь точный план реализации для этой задачи", OperatorStrategy.implementation_plan),
    # patch_proposal
    ("Предложи патч для исправления бага",   OperatorStrategy.patch_proposal),
])
def test_classify_russian_phrases(phrase: str, expected_strategy: OperatorStrategy) -> None:
    result = classify_operator_strategy(phrase)
    assert result is expected_strategy, (
        f"Expected {expected_strategy!r} for {phrase!r}, got {result!r}"
    )


# ---------------------------------------------------------------------------
# Explicit documentation requests → general_question (LLM reads the docs)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "Расскажи по README, что ты умеешь",
    "Проверь свои возможности по документации",
    "Что ты умеешь по документу",
    "Прочитай README и опиши архитектуру",
    "по документации объясни плановщик",
])
def test_explicit_docs_request_yields_general_question(phrase: str) -> None:
    result = classify_operator_strategy(phrase)
    assert result is OperatorStrategy.general_question, (
        f"Explicit docs phrase should map to general_question, got {result!r} for {phrase!r}"
    )


# ---------------------------------------------------------------------------
# Unrecognised text → general_question
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "Как работает алгоритм Дейкстры?",
    "What is the capital of France?",
    "Explain TCP/IP",
    "",
    "   ",
    "random gibberish 12345",
])
def test_unrecognised_text_yields_general_question(phrase: str) -> None:
    result = classify_operator_strategy(phrase)
    assert result is OperatorStrategy.general_question


# ---------------------------------------------------------------------------
# is_local_strategy helper
# ---------------------------------------------------------------------------

def test_is_local_strategy_returns_true_for_all_non_general_members() -> None:
    for strategy in OperatorStrategy:
        if strategy is OperatorStrategy.general_question:
            assert not is_local_strategy(strategy)
        else:
            assert is_local_strategy(strategy)


# ---------------------------------------------------------------------------
# LOCAL_STRATEGIES coverage
# ---------------------------------------------------------------------------

def test_local_strategies_covers_all_non_general_members() -> None:
    expected = frozenset(
        s for s in OperatorStrategy if s is not OperatorStrategy.general_question
    )
    assert LOCAL_STRATEGIES == expected


# ---------------------------------------------------------------------------
# classify_operator_strategy never raises
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    None,  # type: ignore[list-item]
    42,    # type: ignore[list-item]
    "ok",
    "\x00\xff",
    "A" * 10_000,
])
def test_classify_never_raises(text: object) -> None:
    try:
        result = classify_operator_strategy(text)  # type: ignore[arg-type]
    except Exception as exc:
        pytest.fail(f"classify_operator_strategy raised {type(exc).__name__}: {exc}")
    assert isinstance(result, OperatorStrategy)


# ---------------------------------------------------------------------------
# Enum integrity: all members are strings (OperatorStrategy is str, Enum)
# ---------------------------------------------------------------------------

def test_all_strategy_members_are_strings() -> None:
    for s in OperatorStrategy:
        assert isinstance(s.value, str)
        assert s.value == s.value.strip()


# ---------------------------------------------------------------------------
# Shell command hint routes locally (does NOT go to LLM)
# ---------------------------------------------------------------------------

def test_shell_command_hint_is_local() -> None:
    result = classify_operator_strategy("pytest tests/")
    assert result is OperatorStrategy.shell_command_hint
    assert is_local_strategy(result)
