"""Model router tests.

The router is the boundary that lets the agent core swap/compare models by
role instead of binding the whole runtime to one backend.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.model_router import (
    ModelRole,
    ModelRoute,
    ModelRouter,
    UsageTrackedLLM,
    ensure_known_model_role,
)
from core.model_usage import ModelUsageLedger
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool

#: Ambient operator pins the router genuinely honours. Live 2026-08-17: the
#: engine exam pinned Sol/Terra via env and six of these tests went red
#: INSIDE the exam session — both engines then reported "model routing is
#: broken" as their main limitation. A test must build its own env, not
#: inherit the operator's (тесты целят органы в полигоны).
_AMBIENT_PIN_VARS = (
    "AGENT_PROVIDER", "AGENT_MODEL",
    "AGENT_PLANNER_PROVIDER", "AGENT_PLANNER_MODEL",
    "AGENT_SYNTHESIZER_PROVIDER", "AGENT_SYNTHESIZER_MODEL",
    "AGENT_REPAIR_PROVIDER", "AGENT_REPAIR_MODEL",
    "AGENT_OPENAI_REASONING_EFFORT",
    "AGENT_TIER_PROVIDERS_LIGHT", "AGENT_TIER_PROVIDERS_STANDARD",
    "AGENT_TIER_PROVIDERS_DEEP",
)


@pytest.fixture(autouse=True)
def _clean_ambient_pins(monkeypatch):
    for name in _AMBIENT_PIN_VARS:
        monkeypatch.delenv(name, raising=False)


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


def test_balanced_policy_uses_builtin_low_cost_routes_when_available(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_POLICY", "balanced")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")

    # Keep builtin balanced routing isolated from role overrides in .env.
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
    summary = router.routing_summary()

    assert summary["planner"]["provider"] == "anthropic"
    assert summary["planner"]["reason"] == "policy:balanced:anthropic-default"
    assert summary["memory_summary"]["provider"] == "openai"
    assert summary["memory_summary"]["model"] == "gpt-4o-mini"
    assert summary["memory_summary"]["reason"] == "policy:balanced:openai-default-small"
    assert summary["verifier"]["provider"] == "openai"


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


# ── Routing attribution integrity (teaching-harness repair) ──────────────────
#
# The factory may silently heal a forbidden/uncredentialed route to another
# provider/model. The route metadata + usage ledger must never claim a model the
# client did not actually call: either requested == actual, or a truthful
# downgrade reason is stamped.


def _tracked(route: ModelRoute, *, provider: str, model: str) -> UsageTrackedLLM:
    llm = FakeLLM(responses=["ok"])
    llm.provider = provider
    llm.model = model
    return UsageTrackedLLM(
        llm,
        role=route.role,
        route=route,
        cost_tier="medium",
        ledger=ModelUsageLedger(),
    )


def test_tracked_llm_stamps_downgrade_when_client_diverges_from_request():
    route = ModelRoute(
        role=ModelRole.PLANNER.value,
        provider="anthropic",
        model="claude-sonnet-4-5",
        reason="diagnostic:forced-sonnet-4-5",
    )
    tracked = _tracked(route, provider="openai", model="gpt-4o-mini")

    # Attribution must reflect the *actual* client, not the requested model.
    assert tracked.provider == "openai"
    assert tracked.model == "gpt-4o-mini"
    downgrade = (
        "route_downgrade:requested=anthropic/claude-sonnet-4-5"
        "->actual=openai/gpt-4o-mini"
    )
    assert downgrade in tracked.route.reason
    assert "diagnostic:forced-sonnet-4-5" in tracked.route.reason

    attribution = tracked.attribution()
    assert attribution["requested_provider"] == "anthropic"
    assert attribution["requested_model"] == "claude-sonnet-4-5"
    assert attribution["actual_provider"] == "openai"
    assert attribution["actual_model"] == "gpt-4o-mini"


def test_tracked_llm_ledger_never_pairs_forced_reason_with_other_model():
    route = ModelRoute(
        role=ModelRole.PLANNER.value,
        provider="anthropic",
        model="claude-sonnet-4-5",
        reason="diagnostic:forced-sonnet-4-5",
    )
    tracked = _tracked(route, provider="openai", model="gpt-4o-mini")
    tracked.complete(system="s", user="u", max_tokens=8)

    records = tracked.ledger.records
    assert records, "usage ledger recorded nothing"
    last = records[-1]
    # The logged model and its route_reason must agree: model gpt-4o-mini must
    # carry the honest downgrade, never a bare 'forced-sonnet-4-5'.
    assert last.model == "gpt-4o-mini"
    assert last.provider == "openai"
    assert "route_downgrade:" in last.route_reason


def test_tracked_llm_leaves_role_default_route_untouched():
    # Role-default routes leave provider/model as None; there is no explicit
    # request to contradict, so no downgrade annotation should be added.
    route = ModelRoute(role=ModelRole.PLANNER.value, reason="default")
    tracked = _tracked(route, provider="openai", model="gpt-4o-mini")

    assert tracked.route.reason == "default"
    attribution = tracked.attribution()
    assert attribution["requested_provider"] is None
    assert attribution["actual_provider"] == "openai"


def _tier_factory(provider: str | None, model: str | None) -> FakeLLM:
    llm = FakeLLM()
    llm.provider = provider or "unset"
    llm.model = model or "unset"
    return llm


def _isolate_tier_env(monkeypatch) -> None:
    """Neutralise ambient catalog/tier env so tier routing is deterministic."""
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", "/nonexistent/path/catalog.json")
    for tier in ("LIGHT", "STANDARD", "DEEP"):
        monkeypatch.delenv(f"AGENT_MODEL_TIER_{tier}", raising=False)
        monkeypatch.delenv(f"AGENT_TIER_PROVIDERS_{tier}", raising=False)
    for var in ("AGENT_MODEL", "AGENT_PLANNER_PROVIDER", "AGENT_PLANNER_MODEL"):
        monkeypatch.delenv(var, raising=False)


def test_operator_tier_provider_survives_missing_catalog_entry(monkeypatch):
    # Catalog discovery only covers providers that have a fetcher (anthropic,
    # openai). An operator who names `local` in AGENT_TIER_PROVIDERS_LIGHT has
    # already declared its model in LOCAL_LLM_MODEL, so "the catalog cannot
    # describe it" must not silently hand the call to a different provider.
    from core.task_complexity import ComplexityTier

    _isolate_tier_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "qwen-local")
    monkeypatch.setenv("AGENT_TIER_PROVIDERS_LIGHT", "local,openai")

    router = ModelRouter.from_env(llm_factory=_tier_factory)
    provider, _reason, skipped = router._resolve_tier_provider(
        ComplexityTier.LIGHT, ModelRole.PLANNER.value
    )

    assert provider == "local"
    assert "no_model:local" not in skipped


def test_operator_tier_provider_reaches_for_task_with_declared_model(monkeypatch):
    # End-to-end: the explicitly named provider must actually serve the call,
    # using the model the operator declared for it.
    _isolate_tier_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "qwen-local")
    monkeypatch.setenv("AGENT_TIER_PROVIDERS_LIGHT", "local,openai")

    router = ModelRouter.from_env(llm_factory=_tier_factory)
    llm = router.for_task(ModelRole.PLANNER, "привет")

    assert llm.provider == "local"
    assert llm.model == "qwen-local"


def test_cost_tier_stays_unknown_when_nothing_declares_the_model():
    # Honesty guard: "unknown" must remain reachable. With no model name there
    # is nothing to classify, so the router must not invent a band.
    router = ModelRouter(llm_factory=_tier_factory)
    route = ModelRoute(
        role=ModelRole.PLANNER.value,
        provider="anthropic",
        model="",
        reason="empty",
    )

    assert router._cost_tier_for_route(route) == "unknown"


def test_max_cost_limit_binds_on_the_complexity_route(monkeypatch):
    # AGENT_MODEL_MAX_COST was only ever consulted by registry selection
    # (_within_cost_limit, reached through best_for_role). The complexity route
    # picks its model from the catalog and never asked, so an operator ceiling
    # was silently ignored on exactly the path that serves normal traffic.
    #
    # Measured live: with AGENT_MODEL_MAX_COST=free the router still routed to
    # anthropic/claude-sonnet-5 and billed it "medium" — contradicting itself
    # inside a single call.
    from core.task_complexity import ComplexityTier

    _isolate_tier_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("AGENT_TIER_PROVIDERS_STANDARD", "anthropic")
    monkeypatch.setenv("AGENT_MODEL_TIER_STANDARD", "claude-sonnet-5")
    monkeypatch.setenv("AGENT_MODEL_MAX_COST", "free")

    router = ModelRouter.from_env(llm_factory=_tier_factory)
    provider, _reason, skipped = router._resolve_tier_provider(
        ComplexityTier.STANDARD, ModelRole.PLANNER.value
    )

    # claude-sonnet-5 classifies "standard" → cost band "medium" > "free".
    assert provider != "anthropic"
    assert "cost_limit:anthropic" in skipped


def test_max_cost_limit_allows_a_model_inside_the_ceiling(monkeypatch):
    # Guard: the ceiling must not become a blanket ban on the complexity route.
    # A model at or below the limit still routes normally.
    from core.task_complexity import ComplexityTier

    _isolate_tier_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("AGENT_TIER_PROVIDERS_STANDARD", "anthropic")
    monkeypatch.setenv("AGENT_MODEL_TIER_STANDARD", "claude-sonnet-5")
    monkeypatch.setenv("AGENT_MODEL_MAX_COST", "medium")

    router = ModelRouter.from_env(llm_factory=_tier_factory)
    provider, _reason, skipped = router._resolve_tier_provider(
        ComplexityTier.STANDARD, ModelRole.PLANNER.value
    )

    assert provider == "anthropic"
    assert "cost_limit:anthropic" not in skipped


def test_deep_downgrade_keeps_the_operator_standard_provider(monkeypatch):
    # The deep gate downgrades an ungrounded DEEP request to the standard tier.
    # Its docstring says it still serves "the normal standard-tier model", but
    # it called _for_role_with_reason, which resolves the ROLE DEFAULT and never
    # consults AGENT_TIER_PROVIDERS_STANDARD.
    #
    # Measured live with AGENT_TIER_PROVIDERS_STANDARD=anthropic:
    #   ordinary question -> anthropic/claude-sonnet-5   (preference honoured)
    #   harder question   -> openai/gpt-5.4-mini         (preference discarded)
    # The router switched away from the requested provider exactly when the
    # work was hardest.
    from core.task_complexity import ComplexityTier

    _isolate_tier_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("AGENT_PROVIDER", "openai")
    monkeypatch.setenv("AGENT_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("AGENT_TIER_PROVIDERS_STANDARD", "anthropic")
    monkeypatch.setenv("AGENT_MODEL_TIER_STANDARD", "claude-sonnet-5")

    router = ModelRouter.from_env(llm_factory=_tier_factory)
    router.usage_ledger = ModelUsageLedger()
    # No escalation reason -> the gate downgrades DEEP to standard.
    tracked = router.for_task(
        ModelRole.PLANNER, "anything", force_tier=ComplexityTier.DEEP
    )

    assert tracked.route.provider == "anthropic"
    assert tracked.route.reason.startswith("deep_downgraded:")


def test_deep_downgrade_falls_back_to_role_default_without_a_standard_provider(
    monkeypatch,
):
    # Guard: with no operator standard preference the downgrade must behave
    # exactly as before and land on the role default.
    from core.task_complexity import ComplexityTier

    _isolate_tier_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_PROVIDER", "openai")
    monkeypatch.setenv("AGENT_MODEL", "gpt-5.4-mini")

    router = ModelRouter.from_env(llm_factory=_tier_factory)
    router.usage_ledger = ModelUsageLedger()
    tracked = router.for_task(
        ModelRole.PLANNER, "anything", force_tier=ComplexityTier.DEEP
    )

    assert tracked.route.provider == "openai"
    assert tracked.route.reason.startswith("deep_downgraded:")


def _ceiling_registry_json() -> str:
    """Registry with one over-ceiling model and one affordable alternative."""
    return json.dumps([
        {
            "id": "opus-expensive",
            "provider": "anthropic",
            "model": "claude-opus-4-20250514",
            "roles": ["repair_proposal"],
            "quality_tier": "frontier",
            "cost_tier": "high",
        },
        {
            "id": "cheap-repairer",
            "provider": "openai",
            "model": "gpt-cheap-repairer",
            "roles": ["repair_proposal"],
            "quality_tier": "standard",
            "cost_tier": "low",
        },
    ])


def test_cost_ceiling_binds_on_the_role_route(monkeypatch):
    # AGENT_MODEL_MAX_COST filtered only the *preferences* inside
    # _resolve_tier_provider. Every branch that fails there ends in the role
    # route (for_role / _for_role_with_reason), and that path never asked about
    # the ceiling — so the limit was bypassed exactly when it was supposed to
    # bind: once all affordable candidates had been rejected.
    #
    # Measured live with the operator's real .env: AGENT_MODEL_MAX_COST=low
    # plus AGENT_REPAIR_PROVIDER/MODEL still returned
    # anthropic/claude-opus-4-20250514 priced "high", on both for_role and
    # for_task ("...|fallback:role_default").
    _isolate_tier_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("AGENT_MODEL_REGISTRY_JSON", _ceiling_registry_json())
    monkeypatch.setenv("AGENT_MODEL_MAX_COST", "low")
    monkeypatch.setenv("AGENT_REPAIR_PROVIDER", "anthropic")
    monkeypatch.setenv("AGENT_REPAIR_MODEL", "claude-opus-4-20250514")

    router = ModelRouter.from_env(llm_factory=_tier_factory)
    llm = router.for_role(ModelRole.REPAIR_PROPOSAL)

    # The ceiling must move the call off the over-priced model.
    assert (llm.provider, llm.model) != ("anthropic", "claude-opus-4-20250514")
    assert (llm.provider, llm.model) == ("openai", "gpt-cheap-repairer")


def test_cost_ceiling_records_why_the_role_route_was_downgraded(monkeypatch):
    # A silent substitution would trade one lie for another: the ledger has to
    # say which model the ceiling refused.
    _isolate_tier_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("AGENT_MODEL_REGISTRY_JSON", _ceiling_registry_json())
    monkeypatch.setenv("AGENT_MODEL_MAX_COST", "low")
    monkeypatch.setenv("AGENT_REPAIR_PROVIDER", "anthropic")
    monkeypatch.setenv("AGENT_REPAIR_MODEL", "claude-opus-4-20250514")

    router = ModelRouter.from_env(
        llm_factory=_tier_factory, usage_ledger=ModelUsageLedger()
    )
    llm = router.for_role(ModelRole.REPAIR_PROPOSAL)

    assert "cost_limit" in llm.route.reason
    assert "claude-opus-4-20250514" in llm.route.reason
    assert llm.cost_tier == "low"


def test_role_route_inside_the_ceiling_is_untouched(monkeypatch):
    # Guard: the ceiling must not become a blanket ban on explicit role routes.
    _isolate_tier_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("AGENT_MODEL_REGISTRY_JSON", _ceiling_registry_json())
    monkeypatch.setenv("AGENT_MODEL_MAX_COST", "low")
    monkeypatch.setenv("AGENT_REPAIR_PROVIDER", "openai")
    monkeypatch.setenv("AGENT_REPAIR_MODEL", "gpt-cheap-repairer")

    router = ModelRouter.from_env(llm_factory=_tier_factory)
    llm = router.for_role(ModelRole.REPAIR_PROPOSAL)

    assert (llm.provider, llm.model) == ("openai", "gpt-cheap-repairer")


def test_role_route_without_a_ceiling_is_unchanged(monkeypatch):
    # Guard: with no ceiling configured the route is byte-for-byte the old one,
    # expensive model included.
    _isolate_tier_env(monkeypatch)
    monkeypatch.delenv("AGENT_MODEL_MAX_COST", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("AGENT_MODEL_REGISTRY_JSON", _ceiling_registry_json())
    monkeypatch.setenv("AGENT_REPAIR_PROVIDER", "anthropic")
    monkeypatch.setenv("AGENT_REPAIR_MODEL", "claude-opus-4-20250514")

    router = ModelRouter.from_env(llm_factory=_tier_factory)
    llm = router.for_role(ModelRole.REPAIR_PROPOSAL)

    assert (llm.provider, llm.model) == ("anthropic", "claude-opus-4-20250514")


# ---------------------------------------------------------------------------
# A role name that does not exist must not be indistinguishable from a role
# that exists but is simply unconfigured.
#
# `model_role` is a free string on both TeamContract and SubAgentContract, and
# neither validates it — so a typo in a plan (which a model may have written)
# reaches route_for() as data. The fallback to the default route is correct:
# the agent should not crash mid-answer over a misspelling. What is wrong is
# that the fallback is silent and reports the same `reason` as a deliberate
# default, so nothing downstream can tell "you asked for a role I do not know"
# from "that role has no configuration".
#
# The signal goes on the route reason rather than into a log line, following
# the rule already stated in model_router: the router has no logger of its own,
# and the route reason is written to the usage ledger with every call, so a
# second channel would only be a second thing to keep in sync.
# ---------------------------------------------------------------------------


def test_a_blank_role_is_refused_where_the_router_would_refuse_it_too() -> None:
    # An empty value means "no preference" and stays legal. A whitespace-only
    # one does not: it is truthy for callers yet empty for _coerce_role, which
    # raises. Refusing it here keeps the guard from being weaker than routing.
    ensure_known_model_role(None)
    ensure_known_model_role("")

    with pytest.raises(ValueError, match="model_role"):
        ensure_known_model_role("   ")
