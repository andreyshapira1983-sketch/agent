"""Model router tests.

The router is the boundary that lets the agent core swap/compare models by
role instead of binding the whole runtime to one backend.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.model_router import ModelRole, ModelRoute, ModelRouter
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool


def _proposal_json() -> str:
    return json.dumps({
        "diagnosis": "answer() returns 41 while the failing test expects 42",
        "target_file": "buggy.py",
        "proposed_content": "def answer():\n    return 42\n",
        "evidence": ["tests/test_buggy.py::test_answer failed"],
        "confidence": 0.82,
    })


def _seed_repair_workspace(workspace: Path) -> None:
    (workspace / "tests").mkdir()
    (workspace / "buggy.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
    (workspace / "tests" / "test_buggy.py").write_text(
        "from buggy import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
        encoding="utf-8",
    )


def _fake_failing_pytest(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        class C:
            returncode = 1
            stdout = b"1 failed in 0.01s\nFAILED tests/test_buggy.py::test_answer - AssertionError\n"
            stderr = b""

        return C()

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_single_router_preserves_legacy_one_llm_behavior():
    llm = FakeLLM()
    router = ModelRouter.single(llm)

    assert router.for_role(ModelRole.PLANNER) is llm
    assert router.for_role(ModelRole.SYNTHESIZER) is llm
    assert router.for_role(ModelRole.REPAIR_PROPOSAL) is llm
    assert router.routing_summary()[ModelRole.PLANNER.value]["provider"] == "fake"


def test_router_reads_default_and_role_specific_env(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "mock")
    monkeypatch.setenv("AGENT_MODEL", "base-model")
    monkeypatch.setenv("AGENT_PLANNER_MODEL", "planner-model")
    monkeypatch.setenv("AGENT_REPAIR_PROVIDER", "openai")
    monkeypatch.setenv("AGENT_REPAIR_MODEL", "repair-model")

    created: list[tuple[str | None, str | None]] = []

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        created.append((provider, model))
        llm = FakeLLM()
        llm.provider = provider or "anthropic"
        llm.model = model or "claude-sonnet-4-5"
        return llm

    router = ModelRouter.from_env(llm_factory=factory)

    planner = router.for_role(ModelRole.PLANNER)
    synth = router.for_role(ModelRole.SYNTHESIZER)
    repair = router.for_role(ModelRole.REPAIR_PROPOSAL)

    assert (planner.provider, planner.model) == ("mock", "planner-model")
    assert (synth.provider, synth.model) == ("mock", "base-model")
    assert (repair.provider, repair.model) == ("openai", "repair-model")
    assert created == [
        ("mock", "planner-model"),
        ("mock", "base-model"),
        ("openai", "repair-model"),
    ]


def test_router_reuses_one_model_instance_for_identical_routes():
    created = 0

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        nonlocal created
        created += 1
        llm = FakeLLM()
        llm.provider = provider or "mock"
        llm.model = model or "same"
        return llm

    router = ModelRouter(
        default_provider="mock",
        default_model="same",
        llm_factory=factory,
    )

    assert router.for_role(ModelRole.PLANNER) is router.for_role(ModelRole.SYNTHESIZER)
    assert router.for_role(ModelRole.REPAIR_PROPOSAL) is router.for_role(ModelRole.PLANNER)
    assert created == 1


def test_router_can_select_new_model_from_registry_json(monkeypatch):
    monkeypatch.delenv("AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    # Pin the routing policy: this test asserts the *conservative* default
    # reason, so it must not inherit an ambient AGENT_MODEL_POLICY (e.g. the
    # daemon's .env sets it to "balanced").
    monkeypatch.delenv("AGENT_MODEL_POLICY", raising=False)
    monkeypatch.delenv("AGENT_MODEL_MAX_COST", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv(
        "AGENT_MODEL_REGISTRY_JSON",
        json.dumps([
            {
                "id": "future-coder",
                "provider": "openai",
                "model": "gpt-future-coder",
                "roles": ["planner", "repair_proposal"],
                "quality_tier": "frontier",
                "cost_tier": "medium",
                "context_window": 256000,
            }
        ]),
    )

    created: list[tuple[str | None, str | None]] = []

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        created.append((provider, model))
        llm = FakeLLM()
        llm.provider = provider or "anthropic"
        llm.model = model or "claude-sonnet-4-5"
        return llm

    router = ModelRouter.from_env(llm_factory=factory)

    planner = router.for_role(ModelRole.PLANNER)
    synth = router.for_role(ModelRole.SYNTHESIZER)
    summary = router.routing_summary()

    assert (planner.provider, planner.model) == ("openai", "gpt-future-coder")
    assert summary["planner"]["reason"] == "policy:conservative:future-coder"
    # No custom synthesizer route exists, so the router keeps the normal LLM
    # default path instead of guessing.
    assert synth.provider == "anthropic"
    assert created[0] == ("openai", "gpt-future-coder")


def test_router_can_select_new_model_from_registry_file(tmp_path: Path, monkeypatch):
    registry_path = tmp_path / "model_registry.json"
    registry_path.write_text(
        json.dumps({
            "models": [
                {
                    "id": "fresh-planner",
                    "provider": "openai",
                    "model": "gpt-fresh-planner",
                    "roles": ["planner"],
                    "quality_tier": "frontier",
                    "cost_tier": "medium",
                }
            ]
        }),
        encoding="utf-8",
    )
    # Same isolation as above: assert the conservative default, so strip any
    # ambient policy override leaking from the environment / .env.
    monkeypatch.delenv("AGENT_MODEL_POLICY", raising=False)
    monkeypatch.delenv("AGENT_MODEL_MAX_COST", raising=False)
    monkeypatch.setenv("AGENT_MODEL_REGISTRY_PATH", str(registry_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        llm = FakeLLM()
        llm.provider = provider or "anthropic"
        llm.model = model or "claude-sonnet-4-5"
        return llm

    router = ModelRouter.from_env(llm_factory=factory)
    summary = router.routing_summary([ModelRole.PLANNER])
    registry = router.registry_summary()

    assert summary["planner"]["provider"] == "openai"
    assert summary["planner"]["model"] == "gpt-fresh-planner"
    assert summary["planner"]["reason"] == "policy:conservative:fresh-planner"
    assert any(
        spec["id"] == "fresh-planner" and spec["source"].startswith("file:")
        for spec in registry["models"]
    )


def test_role_env_override_wins_over_model_registry(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "mock")
    monkeypatch.setenv("AGENT_MODEL", "base")
    monkeypatch.setenv("AGENT_PLANNER_PROVIDER", "openai")
    monkeypatch.setenv("AGENT_PLANNER_MODEL", "explicit-planner")
    monkeypatch.setenv(
        "AGENT_MODEL_REGISTRY_JSON",
        json.dumps([
            {
                "id": "registry-planner",
                "provider": "anthropic",
                "model": "registry-model",
                "roles": ["planner"],
                "quality_tier": "frontier",
            }
        ]),
    )

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        llm = FakeLLM()
        llm.provider = provider or "unset"
        llm.model = model or "unset"
        return llm

    router = ModelRouter.from_env(llm_factory=factory)
    planner = router.for_role(ModelRole.PLANNER)

    assert (planner.provider, planner.model) == ("openai", "explicit-planner")
    assert router.routing_summary()["planner"]["reason"] == "env:AGENT_PLANNER"


def test_balanced_policy_uses_builtin_low_cost_routes_when_available(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_POLICY", "balanced")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        llm = FakeLLM()
        llm.provider = provider or "unset"
        llm.model = model or "unset"
        return llm

    router = ModelRouter.from_env(llm_factory=factory)
    summary = router.routing_summary()

    assert summary["planner"]["provider"] == "anthropic"
    assert summary["planner"]["reason"] == "policy:balanced:anthropic-default"
    assert summary["memory_summary"]["provider"] == "openai"
    assert summary["memory_summary"]["model"] == "gpt-4o-mini"
    assert summary["memory_summary"]["reason"] == "policy:balanced:openai-default-small"
    assert summary["verifier"]["provider"] == "openai"


def test_balanced_policy_can_fall_back_to_available_provider_for_planning(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_POLICY", "balanced")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        llm = FakeLLM()
        llm.provider = provider or "unset"
        llm.model = model or "unset"
        return llm

    router = ModelRouter.from_env(llm_factory=factory)
    summary = router.routing_summary([ModelRole.PLANNER])

    assert summary["planner"]["provider"] == "openai"
    assert summary["planner"]["model"] == "gpt-4o-mini"
    assert summary["planner"]["reason"] == "policy:balanced:openai-default-small"


def test_light_task_uses_openai_default_small_when_available(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_POLICY", "balanced")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", "/nonexistent/path/catalog.json")
    monkeypatch.delenv("AGENT_MODEL_TIER_LIGHT", raising=False)

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        llm = FakeLLM()
        llm.provider = provider or "unset"
        llm.model = model or "unset"
        return llm

    router = ModelRouter.from_env(llm_factory=factory)
    llm = router.for_task(ModelRole.PLANNER, "привет")

    assert llm.provider == "openai"
    assert llm.model == "gpt-4o-mini"


def test_standard_task_stays_on_anthropic_balanced_route(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_POLICY", "balanced")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    # Isolate from the ambient config/model_catalog.json: its contents (and its
    # freshness/TTL) must not steer this test. Without a live catalog the router
    # falls back to the builtin registry's balanced routes, which is exactly the
    # decision under test. This keeps the test deterministic across
    # `:refresh-models` runs and calendar time.
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", "/nonexistent/path/catalog.json")
    for tier in ("LIGHT", "STANDARD", "DEEP"):
        monkeypatch.delenv(f"AGENT_MODEL_TIER_{tier}", raising=False)

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        llm = FakeLLM()
        llm.provider = provider or "unset"
        llm.model = model or "unset"
        return llm

    router = ModelRouter.from_env(llm_factory=factory)
    llm = router.for_task(ModelRole.PLANNER, "напиши функцию сортировки для списка чисел")

    # Intent: a standard-tier task stays on Anthropic under the balanced policy.
    # Assert the routing decision (provider), not a specific model version, so a
    # newer Claude model never breaks this test.
    assert llm.provider == "anthropic"
    assert llm.model.startswith("claude-")


def test_cost_policy_respects_max_cost_and_model_availability(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_POLICY", "cost")
    monkeypatch.setenv("AGENT_MODEL_MAX_COST", "low")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_PROVIDER", raising=False)
    monkeypatch.setenv(
        "AGENT_MODEL_REGISTRY_JSON",
        json.dumps([
            {
                "id": "cheap-summary",
                "provider": "openai",
                "model": "gpt-cheap",
                "roles": ["memory_summary"],
                "quality_tier": "standard",
                "cost_tier": "low",
            },
            {
                "id": "local-summary",
                "provider": "mock",
                "model": "mock-1",
                "roles": ["memory_summary"],
                "quality_tier": "cheap",
                "cost_tier": "free",
            },
        ]),
    )

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        llm = FakeLLM()
        llm.provider = provider or "anthropic"
        llm.model = model or "claude-sonnet-4-5"
        return llm

    router = ModelRouter.from_env(llm_factory=factory)
    summary = router.routing_summary([ModelRole.MEMORY_SUMMARY])

    # OpenAI is below the cost cap, but it is not selectable without its key.
    # Mock is not selected in real policies unless explicitly allowed.
    assert summary["memory_summary"]["reason"] == "default"
    assert summary["memory_summary"]["provider"] == "anthropic"


def test_offline_policy_can_select_mock_route(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_POLICY", "offline")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        llm = FakeLLM()
        llm.provider = provider or "fallback"
        llm.model = model or "fallback"
        return llm

    router = ModelRouter.from_env(llm_factory=factory)
    summary = router.routing_summary([ModelRole.PLANNER, ModelRole.SYNTHESIZER])

    assert summary["planner"]["provider"] == "mock"
    assert summary["planner"]["reason"] == "policy:offline:mock"
    assert summary["synthesizer"]["provider"] == "mock"


def test_agent_loop_uses_repair_proposal_route(tmp_path: Path, monkeypatch):
    _seed_repair_workspace(tmp_path)
    _fake_failing_pytest(monkeypatch)

    synth_llm = FakeLLM(responses=["synth should not be called"])
    repair_llm = FakeLLM(responses=[_proposal_json()])
    planner_llm = FakeLLM(responses=["planner should not be called"])

    def factory(provider: str | None, model: str | None) -> FakeLLM:
        if model == "repair":
            return repair_llm
        if model == "planner":
            return planner_llm
        return synth_llm

    router = ModelRouter(
        default_provider="fake",
        default_model="synth",
        routes={
            ModelRole.PLANNER: ModelRoute(
                role=ModelRole.PLANNER.value,
                provider="fake",
                model="planner",
            ),
            ModelRole.REPAIR_PROPOSAL: ModelRoute(
                role=ModelRole.REPAIR_PROPOSAL.value,
                provider="fake",
                model="repair",
            ),
        },
        llm_factory=factory,
    )

    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=tmp_path))
    registry.register(FileWriteTool(workspace_root=tmp_path))
    trace_id = new_trace_id()
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=synth_llm,
        logger=TraceLogger(trace_id=trace_id, log_dir=tmp_path / "logs", verbose=False),
        planner=FakePlanner([]),
        model_router=router,
        approval_provider=AutoApprover(),
        max_replan_attempts=1,
    )

    report = agent.propose_repair(
        target_path="buggy.py",
        workspace_root=tmp_path,
        test_paths=("tests",),
    )

    assert report.ok
    assert len(repair_llm.calls) == 1
    assert synth_llm.calls == []
    assert planner_llm.calls == []
