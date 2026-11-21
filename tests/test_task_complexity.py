"""Tests for core/task_complexity.py — Task Complexity Assessment."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from core.model_catalog import classify_model
from core.task_complexity import (
    ComplexityTier,
    assess_complexity,
    can_skip_planner,
    matched_light_signals,
    needs_live_grounding,
    tier_label,
)

# ── STANDARD (default) ────────────────────────────────────────────────────────


def test_empty_string_is_standard():
    assert assess_complexity("") == ComplexityTier.STANDARD


def test_whitespace_only_is_standard():
    assert assess_complexity("   \n\t  ") == ComplexityTier.STANDARD


def test_non_string_is_standard():
    # Type guard — shouldn't blow up
    assert assess_complexity(None) == ComplexityTier.STANDARD  # type: ignore[arg-type]
    assert assess_complexity(42) == ComplexityTier.STANDARD    # type: ignore[arg-type]


# ── classify_model — ambiguous / controversial names ─────────────────────────
# These are the cases most likely to be misclassified. Document expected
# behaviour explicitly so regressions are caught immediately.

@pytest.mark.parametrize("model_id,expected", [
    # OpenAI o-series reasoning models — ALL should be DEEP.
    # Reasoning models happen to have "mini" in their name, but they
    # are NOT cheap lightweight models; they use expensive chain-of-thought.
    ("o3-mini",            ComplexityTier.DEEP),   # o3 reasoning, smaller variant
    ("o4-mini",            ComplexityTier.DEEP),   # o4 reasoning, smaller variant
    ("o1-mini",            ComplexityTier.DEEP),   # o1 reasoning, smaller variant
    ("o1-preview",         ComplexityTier.DEEP),   # o1 reasoning preview
    ("o3",                 ComplexityTier.DEEP),   # full o3
    ("o5-mini-2027",       ComplexityTier.DEEP),   # hypothetical future o-series

    # GPT lightweight — should be LIGHT (GPT family, mini suffix, NOT o-series)
    ("gpt-4o-mini",        ComplexityTier.LIGHT),  # classic cheap GPT model
    ("gpt-4.1-mini",       ComplexityTier.LIGHT),  # GPT 4.1 lightweight variant
    ("gpt-4.5-mini",       ComplexityTier.LIGHT),  # hypothetical future GPT mini

    # gpt-4o — standard GPT (o here means omni, not o-series reasoning)
    ("gpt-4o",             ComplexityTier.STANDARD),  # "o" not followed by digit
    ("gpt-4o-2025",        ComplexityTier.STANDARD),  # versioned gpt-4o

    # Anthropic families — unambiguous
    ("claude-haiku",       ComplexityTier.LIGHT),   # haiku family → LIGHT
    ("claude-opus",        ComplexityTier.DEEP),    # opus family → DEEP
    ("claude-sonnet",      ComplexityTier.STANDARD),# sonnet → STANDARD (no pattern)

    # Edge: "o1" inside a word should NOT trigger o-series detection
    ("proto1-model",       ComplexityTier.STANDARD),  # "o1" preceded by "t" (word char)
    ("model-protocol3",    ComplexityTier.STANDARD),  # "o3" inside word

    # Edge: opus in unusual positions
    ("my-opus-model",      ComplexityTier.DEEP),   # opus anywhere → DEEP
])
def test_classify_ambiguous_models(model_id, expected):
    result = classify_model(model_id)
    assert result == expected, (
        f"classify_model({model_id!r}) = {result.value!r}, expected {expected.value!r}"
    )


# ── tier_label ────────────────────────────────────────────────────────────────

def test_tier_label_contains_tier_name():
    assert "light" in tier_label(ComplexityTier.LIGHT)
    assert "standard" in tier_label(ComplexityTier.STANDARD)
    assert "deep" in tier_label(ComplexityTier.DEEP)


# ── ModelRouter.for_task integration ─────────────────────────────────────────


def test_model_router_for_task_deep_task_different_from_standard(
    tmp_path,
    monkeypatch,
):
    """DEEP tier returns a different LLM than STANDARD only when an operator
    escalation unlocks it; without a reason the deep request downgrades."""
    monkeypatch.setenv(
        "AGENT_MODEL_CATALOG_PATH",
        str(tmp_path / "missing-model-catalog.json"),
    )
    monkeypatch.delenv("AGENT_MODEL_TIER_STANDARD", raising=False)
    monkeypatch.delenv("AGENT_MODEL_TIER_DEEP", raising=False)

    from unittest.mock import MagicMock

    from core.deep_escalation import OperatorEscalation
    from core.model_router import ModelRole, ModelRouter

    instances: list[object] = []

    def factory(provider, model):
        m = MagicMock()
        m.provider = provider
        m.model = model
        instances.append(m)
        return m

    router = ModelRouter(
        default_provider="anthropic",
        default_model="claude-sonnet-4-5",
        llm_factory=factory,
    )

    deep_question = "спроектируй архитектуру распределённой системы с аудитом безопасности"
    std_question = "напиши функцию сортировки"

    # Configure a different deep model
    with patch.dict(os.environ, {"AGENT_MODEL_TIER_DEEP": "claude-opus-deep-test"}):
        # Without an operator reason → the deep request downgrades to standard,
        # so it resolves to the SAME standard model instance.
        downgraded = router.for_task(ModelRole.PLANNER, deep_question)
        std_llm = router.for_task(ModelRole.PLANNER, std_question)
        assert downgraded is std_llm

        # With a valid operator escalation → the deep model is actually used.
        escalation = OperatorEscalation(
            reason="planner_multi_file_architecture_change",
            expected_output="architecture_tradeoff",
        )
        deep_llm = router.for_task(
            ModelRole.PLANNER, deep_question, escalation=escalation
        )
    assert deep_llm is not std_llm
    assert deep_llm.model == "claude-opus-deep-test"


# ── needs_live_grounding ──────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "напиши функцию сортировки списка",
    "добавь валидацию в форму регистрации",
    "исправь баг в обработчике ошибок",
    "review the pull request",
    "implement pagination",
    "спланируй API для авторизации",
    "",
    "   ",
])
def test_live_grounding_negative(text):
    assert needs_live_grounding(text) is False, f"Expected live_grounding=False for: {text!r}"


def test_live_grounding_non_string():
    assert needs_live_grounding(None) is False  # type: ignore[arg-type]
    assert needs_live_grounding(42) is False    # type: ignore[arg-type]


# ── can_skip_planner (cheap-path gate) ────────────────────────────────────────


def test_can_skip_planner_handles_non_string_and_empty():
    assert can_skip_planner(None) is False  # type: ignore[arg-type]
    assert can_skip_planner("") is False
    assert can_skip_planner("   ") is False


# ── LIGHT signal word boundaries (regression: substring collisions) ───────────
#
# `_LIGHT_SIGNALS` used to be matched with a raw `signal in normalized`
# substring test. Short English words such as "hi", "hey" and "version"
# therefore matched INSIDE unrelated words — "this", "they", "conversion" —
# and routed genuine engineering work to the cheapest model.
#
# Observed in production: a 100-char root-cause investigation was routed to
# gpt-5.4-nano with route_reason="complexity:light" because the word "this"
# contains "hi".

@pytest.mark.parametrize("text", [
    # The exact class of question that was mis-routed in production.
    ("Investigate the root cause of this recurring self-build failure "
    "and propose a safe repair procedure."),
    # "hi" inside "this"
    "Why is this failing?",
    "Rewrite this function",
    "Ship this patch",
    # "hi" inside "which"
    "Which module owns retries?",
    # "hey" inside "they"
    "They broke the parser",
    # "version" inside "conversion"
    "Check the conversion logic",
    # "hi" inside "history"
    "Explain the history of this bug",
    # "hi" inside "machine"
    "The machine is down, restart it",
])
def test_light_signal_does_not_match_inside_word(text):
    """A LIGHT signal buried inside a longer word must NOT trigger LIGHT."""
    assert assess_complexity(text) is not ComplexityTier.LIGHT, (
        f"Expected non-LIGHT for {text!r}; "
        f"matched signals = {matched_light_signals(text)}"
    )
    assert matched_light_signals(text) == []


def test_matched_light_signals_rejects_non_string():
    assert matched_light_signals(None) == []  # type: ignore[arg-type]
    assert matched_light_signals("") == []


# ── task_role gate (roles that must never get the cheap model) ────────────────


def test_always_light_model_role_still_wins_over_task_role():
    """memory_summary is a model-role override checked before anything else."""
    assert assess_complexity(
        "hi", role="memory_summary", task_role="repair"
    ) is ComplexityTier.LIGHT
