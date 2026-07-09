from __future__ import annotations

from core.model_registry_audit import audit_model_registry
from core.model_router import ModelRouter
from tests.conftest import FakeLLM


def test_model_registry_audit_explains_routes_are_smaller_than_registry(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_POLICY", "balanced")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        llm = FakeLLM()
        llm.provider = provider or "unset"
        llm.model = model or "unset"
        return llm

    router = ModelRouter.from_env(llm_factory=factory)
    audit = audit_model_registry(router)
    payload = audit.to_dict()

    assert payload["catalog_mode"] == "local_config"
    assert payload["live_provider_catalog"] is False
    assert audit.route_count == 5
    assert audit.registry_model_count >= 4
    assert len(audit.unique_route_models) == 2
    assert any("one model per role" in warning for warning in audit.warnings)
    assert "anthropic:claude-sonnet-4-5" in audit.unique_route_models
    assert "openai:gpt-4o-mini" in audit.unique_route_models


def test_model_registry_audit_reports_unavailable_and_unsupported_models(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_POLICY", "quality")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv(
        "AGENT_MODEL_REGISTRY_JSON",
        """
        [
          {
            "id": "needs-openai",
            "provider": "openai",
            "model": "gpt-new",
            "roles": ["planner"],
            "quality_tier": "frontier",
            "cost_tier": "medium"
          },
          {
            "id": "unknown-provider",
            "provider": "newprovider",
            "model": "new-model",
            "roles": ["planner"],
            "quality_tier": "frontier",
            "cost_tier": "medium",
            "requires_env": []
          }
        ]
        """,
    )

    router = ModelRouter.from_env(llm_factory=lambda provider, model: FakeLLM())
    audit = audit_model_registry(router)

    assert any("needs-openai openai/gpt-new" in item for item in audit.unavailable_models)
    assert any(
        "unknown-provider newprovider/new-model" in item
        for item in audit.unsupported_models
    )
    assert any("required environment variables" in warning for warning in audit.warnings)
