from __future__ import annotations

from core.model_registry_audit import audit_model_registry
from core.model_router import ModelRouter
from tests.conftest import FakeLLM


def test_model_registry_audit_explains_routes_are_smaller_than_registry(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_POLICY", "balanced")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")

    # Keep this test independent from role-specific routes loaded from .env.
    for key in (
        "AGENT_PROVIDER",
        "AGENT_MODEL",
        "AGENT_PLANNER_PROVIDER",
        "AGENT_PLANNER_MODEL",
        "AGENT_SYNTHESIZER_PROVIDER",
        "AGENT_SYNTHESIZER_MODEL",
        "AGENT_REPAIR_PROVIDER",
        "AGENT_REPAIR_MODEL",
        "AGENT_MEMORY_PROVIDER",
        "AGENT_MEMORY_MODEL",
        "AGENT_VERIFIER_PROVIDER",
        "AGENT_VERIFIER_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

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


def test_audit_reports_models_the_agent_actually_used(monkeypatch, tmp_path):
    # ROOT C. The audit called its configured route models "active models" and
    # never read the usage ledger. Measured live in this repo at one moment:
    #   :model-registry-audit -> unique_route_models=['openai:gpt-5.4-mini']  (1)
    #   :model-usage          -> 8 distinct models across anthropic/openai/local
    # The audit reported one active model while the agent had demonstrably run
    # eight. Configuration was being presented as behaviour.
    import json
    from pathlib import Path

    from core.model_usage import ModelUsageLedger

    monkeypatch.setenv("AGENT_MODEL_POLICY", "balanced")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    for key in (
        "AGENT_PROVIDER",
        "AGENT_MODEL",
        "AGENT_PLANNER_PROVIDER",
        "AGENT_PLANNER_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    ledger_path = tmp_path / "model_usage.jsonl"
    ledger_path.write_text(
        json.dumps(
            {
                "role": "planner",
                "provider": "anthropic",
                "model": "claude-opus-5",
                "route_reason": "complexity:deep:anthropic",
                "status": "success",
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "cost_tier": "high",
                "cost_units": 8,
                "estimated": False,
                "started_at": "2026-01-01T00:00:00+00:00",
                "completed_at": "2026-01-01T00:00:01+00:00",
                "duration_ms": 1000,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def factory(provider, model):
        llm = FakeLLM()
        llm.provider = provider or "unset"
        llm.model = model or "unset"
        return llm

    router = ModelRouter.from_env(llm_factory=factory)
    router.usage_ledger = ModelUsageLedger(path=Path(ledger_path))

    audit = audit_model_registry(router)
    payload = audit.to_dict()

    # The model the agent really ran must be visible as observed.
    assert "anthropic:claude-opus-5" in payload["observed_models"]
    # ...and flagged as drift, because it is not one of the configured routes.
    assert "anthropic:claude-opus-5" in payload["observed_not_configured"]
    assert "observed" in audit.user_summary()


def test_audit_without_a_usage_ledger_reports_no_observed_models(monkeypatch):
    # Guard: an audit run with no ledger must not invent usage.
    monkeypatch.setenv("AGENT_MODEL_POLICY", "balanced")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")

    def factory(provider, model):
        llm = FakeLLM()
        llm.provider = provider or "unset"
        llm.model = model or "unset"
        return llm

    router = ModelRouter.from_env(llm_factory=factory)
    router.usage_ledger = None

    payload = audit_model_registry(router).to_dict()

    assert payload["observed_models"] == []
    assert payload["observed_not_configured"] == []
