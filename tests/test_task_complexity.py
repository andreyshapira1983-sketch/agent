"""Tests for core/task_complexity.py — Task Complexity Assessment."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from core.task_complexity import (
    ComplexityTier,
    _ALWAYS_LIGHT_ROLES,
    _SHORT_TEXT_THRESHOLD,
    assess_complexity,
    needs_live_grounding,
    tier_label,
)


# ── ComplexityTier enum ───────────────────────────────────────────────────────

def test_tier_enum_values():
    assert ComplexityTier.LIGHT.value    == "light"
    assert ComplexityTier.STANDARD.value == "standard"
    assert ComplexityTier.DEEP.value     == "deep"


def test_tier_is_string():
    # .value returns the plain string; equality with str works via str subclass
    assert ComplexityTier.LIGHT.value == "light"
    assert ComplexityTier.DEEP == "deep"


# ── role-level overrides ──────────────────────────────────────────────────────

def test_memory_summary_always_light():
    heavy = "сделай полный архитектурный аудит всей системы и сравни все варианты"
    assert assess_complexity(heavy, role="memory_summary") == ComplexityTier.LIGHT


def test_always_light_roles_set_not_empty():
    assert "memory_summary" in _ALWAYS_LIGHT_ROLES


# ── LIGHT signals ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "привет",
    "hello",
    "hi",
    "статус",
    "ping",
    "что такое автономный агент",
    "переведи на английский",
    "summary please",
    "суммаризуй кратко",
    "да или нет",
    "yes or no",
    "версия системы",
    "покажи список задач",
])
def test_light_signals(text):
    assert assess_complexity(text) == ComplexityTier.LIGHT, f"Expected LIGHT for: {text!r}"


def test_very_short_text_no_signals_is_standard():
    # Short text with NO recognized signal → STANDARD (conservative default)
    short = "run"
    assert len(short) < _SHORT_TEXT_THRESHOLD
    assert assess_complexity(short) == ComplexityTier.STANDARD


# ── STANDARD (default) ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "спланируй API для сервиса авторизации",
    "напиши код для обработки платежей",
    "добавь валидацию в форму регистрации",
    "исправь баг в обработчике ошибок",
    "реализуй endpoint для загрузки файлов",
    "review the pull request and suggest improvements",
    "implement pagination for the user list",
])
def test_standard_tasks(text):
    assert assess_complexity(text) == ComplexityTier.STANDARD, f"Expected STANDARD for: {text!r}"


def test_empty_string_is_standard():
    assert assess_complexity("") == ComplexityTier.STANDARD


def test_whitespace_only_is_standard():
    assert assess_complexity("   \n\t  ") == ComplexityTier.STANDARD


def test_non_string_is_standard():
    # Type guard — shouldn't blow up
    assert assess_complexity(None) == ComplexityTier.STANDARD  # type: ignore[arg-type]
    assert assess_complexity(42) == ComplexityTier.STANDARD    # type: ignore[arg-type]


# ── DEEP signals ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "спроектируй архитектуру микросервисной платформы",
    "сделай полный аудит безопасности приложения",
    "напиши и протести написать и протестировать весь модуль",
    "разработать с нуля систему авторизации",
    "from scratch build a distributed caching layer",
    "составь стратегию развития продукта на год",  # стратеги → DEEP
    "сравни все варианты хранения данных и оцени риски",
    "research all available vector database solutions and evaluate all",
    "multi-step migration plan for the legacy system",
    "комплексный анализ производительности и стратегия масштабирования",
    "full documentation for the entire API",
    "security audit for the authentication module",
    "conduct a pentest on the web application",
])
def test_deep_signals(text):
    assert assess_complexity(text) == ComplexityTier.DEEP, f"Expected DEEP for: {text!r}"


def test_deep_overrides_light_signal():
    # Even if text contains a light keyword, DEEP signal wins
    text = "кратко спроектируй архитектуру всей системы"
    assert assess_complexity(text) == ComplexityTier.DEEP


# ── model_catalog — classify_model pattern tests ──────────────────────────────
# Verify that the NAMING PATTERN classification works correctly.
# No specific model version numbers are tested here.

from core.model_catalog import classify_model, tier_model_for


def test_classify_haiku_family_is_light():
    # Any model whose name contains "haiku" → LIGHT
    assert classify_model("claude-haiku-4-0") == ComplexityTier.LIGHT
    assert classify_model("claude-haiku-3-5") == ComplexityTier.LIGHT
    assert classify_model("some-haiku-model") == ComplexityTier.LIGHT


def test_classify_mini_family_is_light():
    # "mini" → LIGHT (OpenAI lightweight family + others)
    assert classify_model("gpt-4o-mini-2025") == ComplexityTier.LIGHT
    assert classify_model("future-mini-v3")    == ComplexityTier.LIGHT


def test_classify_flash_and_lite_is_light():
    assert classify_model("gemini-flash-002") == ComplexityTier.LIGHT
    assert classify_model("my-model-lite")    == ComplexityTier.LIGHT


def test_classify_opus_family_is_deep():
    # Any model whose name contains "opus" → DEEP
    assert classify_model("claude-opus-4-1") == ComplexityTier.DEEP
    assert classify_model("claude-opus-9-0") == ComplexityTier.DEEP  # future


def test_classify_thinking_is_deep():
    assert classify_model("claude-3-7-sonnet-20250219-thinking") == ComplexityTier.DEEP


def test_classify_o1_o3_is_deep():
    assert classify_model("o1-preview-2024") == ComplexityTier.DEEP
    assert classify_model("o3-mini")          == ComplexityTier.DEEP


def test_classify_sonnet_is_standard():
    # "sonnet" matches neither LIGHT nor DEEP patterns → STANDARD
    assert classify_model("claude-sonnet-4-5") == ComplexityTier.STANDARD
    assert classify_model("claude-sonnet-99")  == ComplexityTier.STANDARD


def test_classify_gpt4o_is_standard():
    assert classify_model("gpt-4o")      == ComplexityTier.STANDARD
    assert classify_model("gpt-4o-2025") == ComplexityTier.STANDARD


def test_classify_unknown_model_is_standard():
    # Unknown names default to STANDARD (safe/balanced)
    assert classify_model("some-future-model-v7")  == ComplexityTier.STANDARD
    assert classify_model("llama-3-70b-instruct")  == ComplexityTier.STANDARD
    assert classify_model("")                       == ComplexityTier.STANDARD


def test_classify_case_insensitive():
    assert classify_model("Claude-Haiku-4-0")   == ComplexityTier.LIGHT
    assert classify_model("CLAUDE-OPUS-4-1")    == ComplexityTier.DEEP


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



def test_tier_model_for_env_override_light(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_TIER_LIGHT", "claude-haiku-custom")
    model = tier_model_for(ComplexityTier.LIGHT, "anthropic")
    assert model == "claude-haiku-custom"


def test_tier_model_for_env_override_deep(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_TIER_DEEP", "claude-opus-99")
    model = tier_model_for(ComplexityTier.DEEP, "anthropic")
    assert model == "claude-opus-99"


def test_tier_model_for_env_override_strips_whitespace(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_TIER_STANDARD", "  claude-sonnet-custom  ")
    model = tier_model_for(ComplexityTier.STANDARD, "anthropic")
    assert model == "claude-sonnet-custom"


def test_tier_model_for_no_catalog_returns_empty(tmp_path, monkeypatch):
    """Without catalog and without env var, returns empty string."""
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", str(tmp_path / "nonexistent.json"))
    monkeypatch.delenv("AGENT_MODEL_TIER_LIGHT", raising=False)
    result = tier_model_for(ComplexityTier.LIGHT, "anthropic")
    assert result == ""


def test_tier_model_for_env_override_beats_catalog(tmp_path, monkeypatch):
    """Env var wins even when catalog has a different model."""
    import json
    from datetime import datetime, timezone

    catalog_file = tmp_path / "model_catalog.json"
    catalog_file.write_text(json.dumps({
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "providers": {
            "anthropic": {
                "models": [{"id": "claude-haiku-3-5", "tier": "light"}],
                "tier_best": {"light": "claude-haiku-3-5"},
            }
        }
    }), encoding="utf-8")
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", str(catalog_file))
    monkeypatch.setenv("AGENT_MODEL_TIER_LIGHT", "my-custom-haiku")

    result = tier_model_for(ComplexityTier.LIGHT, "anthropic")
    assert result == "my-custom-haiku"


def test_tier_model_for_reads_from_catalog(tmp_path, monkeypatch):
    """Valid catalog → tier_model_for returns the cached model name."""
    import json
    from datetime import datetime, timezone

    catalog_file = tmp_path / "model_catalog.json"
    catalog_file.write_text(json.dumps({
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "providers": {
            "anthropic": {
                "models": [{"id": "claude-haiku-5-0", "tier": "light"}],
                "tier_best": {"light": "claude-haiku-5-0"},
            }
        }
    }), encoding="utf-8")
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", str(catalog_file))
    monkeypatch.delenv("AGENT_MODEL_TIER_LIGHT", raising=False)

    result = tier_model_for(ComplexityTier.LIGHT, "anthropic")
    assert result == "claude-haiku-5-0"


def test_tier_model_for_unknown_provider_returns_empty(tmp_path, monkeypatch):
    """Completely unknown provider → empty string (no crash)."""
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", str(tmp_path / "none.json"))
    monkeypatch.delenv("AGENT_MODEL_TIER_LIGHT", raising=False)
    result = tier_model_for(ComplexityTier.LIGHT, "unknown-provider-xyz")
    assert result == ""


# ── tier_label ────────────────────────────────────────────────────────────────

def test_tier_label_contains_tier_name():
    assert "light" in tier_label(ComplexityTier.LIGHT)
    assert "standard" in tier_label(ComplexityTier.STANDARD)
    assert "deep" in tier_label(ComplexityTier.DEEP)


# ── ModelRouter.for_task integration ─────────────────────────────────────────

def test_model_router_for_task_mock_static():
    """with static_llm (mock mode), for_task always returns the static LLM."""
    from unittest.mock import MagicMock
    from core.model_router import ModelRole, ModelRouter

    fake_llm = MagicMock()
    router = ModelRouter.single(fake_llm)

    result = router.for_task(ModelRole.PLANNER, "спроектируй архитектуру")
    assert result is fake_llm


def test_model_router_for_task_standard_equals_for_role():
    """STANDARD complexity must return same object as for_role()."""
    from core.model_router import ModelRole, ModelRouter

    router = ModelRouter(
        default_provider="mock",
        default_model="mock-1",
    )
    role_llm  = router.for_role(ModelRole.PLANNER)
    task_llm  = router.for_task(ModelRole.PLANNER, "напиши код для API")
    assert role_llm is task_llm


def test_model_router_for_task_light_uses_haiku_model(monkeypatch):
    """LIGHT tier should use whatever model the env var specifies (haiku family).

    Model names are not hardcoded — the env var override is the single source
    of truth when no catalog is available.
    """
    from core.model_router import ModelRole, ModelRouter

    # Provide a LIGHT model via env var (as an operator would do)
    monkeypatch.setenv("AGENT_MODEL_TIER_LIGHT", "claude-haiku-test-model")
    # Ensure no stale catalog leaks into this test
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", "/nonexistent/path/catalog.json")

    calls: list[tuple[str | None, str | None]] = []

    def fake_factory(provider, model):
        calls.append((provider, model))
        from unittest.mock import MagicMock
        m = MagicMock()
        m.provider = provider
        m.model = model
        return m

    router = ModelRouter(
        default_provider="anthropic",
        default_model="claude-sonnet-default",
        llm_factory=fake_factory,
    )
    router.for_task(ModelRole.PLANNER, "привет")
    # Factory must be called with exactly the LIGHT model from env var
    assert any("haiku" in (m or "") for _, m in calls), f"calls: {calls}"


def test_model_router_for_task_deep_task_different_from_standard():
    """DEEP tier should not return the same LLM instance as STANDARD."""
    from unittest.mock import MagicMock
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

    # Configure a different deep model
    with patch.dict(os.environ, {"AGENT_MODEL_TIER_DEEP": "claude-opus-deep-test"}):
        deep_llm  = router.for_task(
            ModelRole.PLANNER,
            "спроектируй архитектуру распределённой системы с аудитом безопасности",
        )
    std_llm = router.for_task(ModelRole.PLANNER, "напиши функцию сортировки")
    # Different task complexity → different model instances
    assert deep_llm is not std_llm


# ── needs_live_grounding ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "latest Claude models 2025",
    "what's the newest GPT model",
    "вышел ли новый Claude",
    "что сейчас лучшая модель",
    "актуальные модели Anthropic",
    "today's best LLM options",
    "лучшая модель в 2026 году",
    "GPT-5 vs Claude 5",
    "recently released AI models",
    "just announced by OpenAI",
    "что нового в AI индустрии",
    "обновление Claude API",
    "current anthropic roadmap",
    "as of 2026 which model should I use",
])
def test_live_grounding_positive(text):
    assert needs_live_grounding(text) is True, f"Expected live_grounding=True for: {text!r}"


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


def test_live_grounding_case_insensitive():
    assert needs_live_grounding("LATEST CLAUDE MODELS") is True
    assert needs_live_grounding("Latest Model 2026") is True


def test_live_grounding_planner_prompt_injection(monkeypatch):
    """When needs_live_grounding=True the planner prompt must contain the LIVE_GROUNDING block."""
    from core.llm import LLM
    from core.planner import LLMPlanner
    from tools.base import ToolRegistry

    captured: list[dict] = []

    class CapturingLLM:
        def complete(self, system: str, user: str, **kw) -> str:
            captured.append({"system": system, "user": user})
            return '{"reasoning": "test", "steps": []}'

    registry = ToolRegistry()
    planner = LLMPlanner(llm=CapturingLLM(), registry=registry)
    planner.plan(
        question="latest Anthropic models 2025",
        file_hint=None,
    )
    assert captured, "LLM was not called"
    user_prompt = captured[0]["user"]
    assert "LIVE_GROUNDING" in user_prompt, "LIVE_GROUNDING hint not injected"


def test_no_live_grounding_planner_prompt_clean():
    """Static coding task must NOT have LIVE_GROUNDING in the prompt."""
    from core.planner import LLMPlanner
    from tools.base import ToolRegistry

    captured: list[dict] = []

    class CapturingLLM:
        def complete(self, system: str, user: str, **kw) -> str:
            captured.append({"user": user})
            return '{"reasoning": "test", "steps": []}'

    planner = LLMPlanner(llm=CapturingLLM(), registry=ToolRegistry())
    planner.plan(question="напиши функцию сортировки", file_hint=None)
    assert "LIVE_GROUNDING" not in captured[0]["user"]

