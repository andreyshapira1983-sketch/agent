"""TD-010: provider-by-complexity routing in ModelRouter.for_task.

These tests prove the router picks a PROVIDER by complexity tier among
already-supported providers (LIGHT→huggingface/openai, STANDARD→openai,
DEEP→anthropic), honoring env overrides and credential availability, while
never hardcoding model names (models always come from the model catalog) and
never making real API calls. Model resolution is stubbed via
``core.model_catalog.tier_model_for`` so the tests are deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.deep_escalation import OperatorEscalation
from core.model_router import ModelRole, ModelRoute, ModelRouter
from core.model_usage import ModelUsageLedger
from core.task_complexity import ComplexityTier
from tests.conftest import FakeLLM


def _factory(provider: str | None, model: str | None) -> FakeLLM:
    llm = FakeLLM()
    llm.provider = provider or "unset"
    llm.model = model or "unset"
    return llm


def _router(tmp_path: Path, routes=None) -> ModelRouter:
    return ModelRouter(
        default_provider="anthropic",
        default_model="role-default-model",
        routes=routes,
        llm_factory=_factory,
        usage_ledger=ModelUsageLedger(path=tmp_path / "usage.jsonl"),
    )


def _stub_tier_models(monkeypatch, mapping: dict[tuple[str, str], str]) -> None:
    """Stub tier_model_for so (tier_value, provider) → model is deterministic."""

    def fake_tier_model_for(tier, provider):  # noqa: ANN001
        return mapping.get((tier.value, provider), "")

    monkeypatch.setattr("core.model_catalog.tier_model_for", fake_tier_model_for)


def _clear_provider_creds(monkeypatch) -> None:
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    for var in (
        "AGENT_TIER_PROVIDERS_LIGHT",
        "AGENT_TIER_PROVIDERS_STANDARD",
        "AGENT_TIER_PROVIDERS_DEEP",
    ):
        monkeypatch.delenv(var, raising=False)


def test_light_prefers_huggingface_when_available(monkeypatch, tmp_path):
    _clear_provider_creds(monkeypatch)
    monkeypatch.setenv("HF_TOKEN", "hf-key")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    _stub_tier_models(monkeypatch, {
        ("light", "huggingface"): "hf-light",
        ("light", "openai"): "oa-light",
    })

    router = _router(tmp_path)
    llm = router.for_task(ModelRole.SYNTHESIZER, "hi", force_tier=ComplexityTier.LIGHT)

    assert llm.route.provider == "huggingface"
    assert llm.route.model == "hf-light"
    assert llm.route.reason == "complexity:light:huggingface"


def test_light_falls_back_to_openai_when_hf_unavailable(monkeypatch, tmp_path):
    _clear_provider_creds(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    _stub_tier_models(monkeypatch, {
        ("light", "huggingface"): "hf-light",
        ("light", "openai"): "oa-light",
    })

    router = _router(tmp_path)
    llm = router.for_task(ModelRole.SYNTHESIZER, "hi", force_tier=ComplexityTier.LIGHT)

    assert llm.route.provider == "openai"
    assert llm.route.model == "oa-light"
    assert llm.route.reason == (
        "complexity:light:openai|skipped:provider_unavailable:huggingface"
    )


def test_standard_routes_to_openai(monkeypatch, tmp_path):
    _clear_provider_creds(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    _stub_tier_models(monkeypatch, {("standard", "openai"): "oa-std"})

    router = _router(tmp_path)
    llm = router.for_task(ModelRole.PLANNER, "task", force_tier=ComplexityTier.STANDARD)

    assert llm.route.provider == "openai"
    assert llm.route.model == "oa-std"
    assert llm.route.reason == "complexity:standard:openai"


def test_deep_routes_to_anthropic_with_operator_escalation(monkeypatch, tmp_path):
    _clear_provider_creds(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "an-key")
    _stub_tier_models(monkeypatch, {("deep", "anthropic"): "an-deep"})

    router = _router(tmp_path)
    escalation = OperatorEscalation(
        reason="planner_multi_file_architecture_change",
        expected_output="architecture_tradeoff",
    )
    llm = router.for_task(
        ModelRole.PLANNER, "design", force_tier=ComplexityTier.DEEP, escalation=escalation
    )

    assert llm.route.provider == "anthropic"
    assert llm.route.model == "an-deep"


def test_deep_without_operator_reason_downgrades_to_standard(monkeypatch, tmp_path):
    _clear_provider_creds(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "an-key")
    _stub_tier_models(monkeypatch, {("deep", "anthropic"): "an-deep"})

    router = _router(tmp_path)
    llm = router.for_task(ModelRole.PLANNER, "design", force_tier=ComplexityTier.DEEP)

    # The deep model must NOT be opened without a valid operator reason.
    assert llm.route.model != "an-deep"
    assert llm.route.model == "role-default-model"


def test_env_override_selects_provider(monkeypatch, tmp_path):
    _clear_provider_creds(monkeypatch)
    monkeypatch.setenv("HF_TOKEN", "hf-key")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    monkeypatch.setenv("AGENT_TIER_PROVIDERS_LIGHT", "openai")
    _stub_tier_models(monkeypatch, {
        ("light", "huggingface"): "hf-light",
        ("light", "openai"): "oa-light",
    })

    router = _router(tmp_path)
    llm = router.for_task(ModelRole.SYNTHESIZER, "hi", force_tier=ComplexityTier.LIGHT)

    assert llm.route.provider == "openai"
    assert llm.route.reason == "complexity:light:openai"


def test_unsupported_provider_local_is_skipped_gracefully(monkeypatch, tmp_path):
    _clear_provider_creds(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    monkeypatch.setenv("AGENT_TIER_PROVIDERS_LIGHT", "local,openai")
    _stub_tier_models(monkeypatch, {("light", "openai"): "oa-light"})

    router = _router(tmp_path)
    llm = router.for_task(ModelRole.SYNTHESIZER, "hi", force_tier=ComplexityTier.LIGHT)

    assert llm.route.provider == "openai"
    assert llm.route.reason == (
        "complexity:light:openai|skipped:provider_unavailable:local"
    )


def test_explicit_role_provider_env_wins(monkeypatch, tmp_path):
    _clear_provider_creds(monkeypatch)
    monkeypatch.setenv("HF_TOKEN", "hf-key")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    _stub_tier_models(monkeypatch, {
        ("light", "huggingface"): "hf-light",
        ("light", "openai"): "oa-light",
        ("light", "anthropic"): "an-light",
    })

    routes = {
        ModelRole.SYNTHESIZER: ModelRoute(
            role="synthesizer", provider="anthropic", model="explicit-model"
        )
    }
    router = _router(tmp_path, routes=routes)
    llm = router.for_task(ModelRole.SYNTHESIZER, "hi", force_tier=ComplexityTier.LIGHT)

    # Explicit role provider must beat the huggingface/openai preference.
    assert llm.route.provider == "anthropic"


def test_no_provider_config_falls_back_to_role_default(monkeypatch, tmp_path):
    _clear_provider_creds(monkeypatch)
    _stub_tier_models(monkeypatch, {})  # no tier model for any provider

    router = _router(tmp_path)
    llm = router.for_task(ModelRole.SYNTHESIZER, "hi", force_tier=ComplexityTier.LIGHT)

    # Nothing available → behaves as before, resolving the role default.
    assert llm.route.model == "role-default-model"


def test_static_llm_is_unchanged(monkeypatch, tmp_path):
    _clear_provider_creds(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-key")
    _stub_tier_models(monkeypatch, {("light", "openai"): "oa-light"})

    static = FakeLLM()
    router = ModelRouter.single(static)
    assert router.for_task(
        ModelRole.SYNTHESIZER, "hi", force_tier=ComplexityTier.LIGHT
    ) is static
