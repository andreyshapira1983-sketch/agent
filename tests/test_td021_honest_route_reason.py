"""TD-021: honest route_reason on the TD-010 complexity-routing fallback.

When no tier model is available (no populated catalog and no AGENT_MODEL_TIER_*
env), for_task must still resolve exactly the same role-default model as before —
but it used to return it stamped with the registry's opaque ``policy:*`` route
id, hiding the fact that complexity routing ran and fell back. These tests prove
the fallback now records the assessed complexity tier, the fallback, and any
skipped providers, while the selected model is unchanged. No real API calls.
"""

from __future__ import annotations

from core.model_router import ModelRole
from core.task_complexity import ComplexityTier

from tests.test_provider_routing_td010 import (
    _clear_provider_creds,
    _router,
    _stub_tier_models,
)


def test_no_tier_model_fallback_reason_is_honest(monkeypatch, tmp_path):
    _clear_provider_creds(monkeypatch)
    _stub_tier_models(monkeypatch, {})  # nothing discoverable for any provider

    router = _router(tmp_path)
    llm = router.for_task(
        ModelRole.SYNTHESIZER, "hi", force_tier=ComplexityTier.LIGHT
    )

    # Model is unchanged — the fix is observability-only.
    assert llm.route.model == "role-default-model"
    # Reason is honest, not the opaque registry policy id.
    assert not llm.route.reason.startswith("policy:")
    assert llm.route.reason.startswith("complexity:light")
    assert "fallback:role_default:no_tier_model" in llm.route.reason
    # Skipped preferred providers are recorded as details.
    assert "skipped:" in llm.route.reason
    assert "provider_unavailable:huggingface" in llm.route.reason


def test_standard_no_pref_fallback_reason_is_honest(monkeypatch, tmp_path):
    _clear_provider_creds(monkeypatch)
    _stub_tier_models(monkeypatch, {})  # openai unavailable → no preferred provider

    router = _router(tmp_path)
    llm = router.for_task(
        ModelRole.PLANNER, "task", force_tier=ComplexityTier.STANDARD
    )

    assert llm.route.model == "role-default-model"
    assert not llm.route.reason.startswith("policy:")
    assert llm.route.reason.startswith("complexity:standard")
    assert "fallback:role_default" in llm.route.reason
    assert "provider_unavailable:openai" in llm.route.reason


def test_fallback_model_matches_for_role(monkeypatch, tmp_path):
    """The honest reason must not change which model is served."""
    _clear_provider_creds(monkeypatch)
    _stub_tier_models(monkeypatch, {})

    router = _router(tmp_path)
    fallback = router.for_task(
        ModelRole.SYNTHESIZER, "hi", force_tier=ComplexityTier.STANDARD
    )
    role_default = router.for_role(ModelRole.SYNTHESIZER)

    assert fallback.route.provider == role_default.route.provider
    assert fallback.route.model == role_default.route.model
