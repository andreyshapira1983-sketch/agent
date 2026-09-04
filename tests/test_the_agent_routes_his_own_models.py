"""The agent routes his own models (operator's word 2026-09-04 22:20).

Keys stay secrets and he never assigns roles to keys; within the allowed
pool he decides which MODEL answers which role, with a reason; the router
reads his decision ahead of the env pins and substitutes the credential;
every route is journaled without secrets; the choice is dynamic — a new
record is picked up without a restart. Pinned here:

  - the pool is the providers with a client in this code AND a present key
    (Google: key, no client → not in the pool; a missing key → not in the pool);
  - the store validates the FORM of a decision (role, pool, model, reason,
    no secret-looking tokens) and never its substance;
  - the router's layering: agent policy → env pin → default; a released
    role falls back to the env pin; a policy naming a provider that lost
    its key falls through, never to an uncredentialed call;
  - the usage ledger carries `agent_policy:<id>` as the route reason;
  - the tool refuses without a reason, refuses secrets, and caps itself.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.model_routing_policy import RoutingPolicyStore, allowed_pool

_ENV = {
    "OPENAI_API_KEY": "sk-secret-openai",
    "DEEPSEEK_API_KEY": "ds-secret",
    "Google_API_KEY": "google-secret",
}


def test_the_pool_is_providers_with_a_client_and_a_key_only():
    pool = allowed_pool(_ENV)
    assert "openai" in pool and "deepseek" in pool
    assert "google" not in pool, "a key without a client is not routable"
    assert "anthropic" not in pool, "a client without a key is not routable"
    assert "local" not in pool
    narrowed = allowed_pool({**_ENV, "AGENT_ROUTING_POOL": "deepseek"})
    assert narrowed == ("deepseek",)
    assert allowed_pool({**_ENV, "AGENT_ROUTING_POOL": "google"}) == (), "the env narrows, never widens"


def test_a_decision_is_stored_with_its_reason_and_read_back_newest_first(tmp_path: Path):
    store = RoutingPolicyStore(tmp_path / "policy.jsonl")
    first = store.set_route(role="planner", provider="deepseek", model="deepseek-chat",
                            reason="261 verified calls today at 1 unit/1k; planner needs breadth, not depth",
                            evidence=["usage ledger 2026-09-04: deepseek 261 ok / 0 errors"], env=_ENV)
    assert first.stored and first.choice is not None
    assert first.choice.route_reason == f"agent_policy:{first.choice.id}"
    second = store.set_route(role="planner", provider="openai", model="gpt-5.6-sol",
                             reason="planner quality: openai verified share 0.9 vs 0.6", env=_ENV)
    assert second.stored
    active = store.resolve("planner")
    assert active is not None and active.provider == "openai" and active.model == "gpt-5.6-sol"
    assert store.resolve("verifier") is None
    assert len(store.history("planner")) == 2


@pytest.mark.parametrize("kwargs,fragment", [
    ({"role": "cook", "provider": "openai", "model": "m", "reason": "r"}, "unknown role"),
    ({"role": "planner", "provider": "google", "model": "gemini-flash", "reason": "cheap"}, "not in the allowed pool"),
    ({"role": "planner", "provider": "anthropic", "model": "claude", "reason": "strong"}, "not in the allowed pool"),
    ({"role": "planner", "provider": "openai", "model": "", "reason": "r"}, "model must be"),
    ({"role": "planner", "provider": "openai", "model": "gpt", "reason": ""}, "reason is required"),
    ({"role": "planner", "provider": "openai", "model": "gpt", "reason": "use key sk-abcdefghijklmnop"}, "secret-looking"),
])
def test_the_form_of_a_decision_is_checked_and_its_substance_is_not(tmp_path: Path, kwargs, fragment):
    store = RoutingPolicyStore(tmp_path / "policy.jsonl")
    verdict = store.set_route(env=_ENV, **kwargs)
    assert not verdict.stored and fragment in verdict.refused_by
    assert store.resolve("planner") is None


def test_a_released_role_falls_back_and_says_why(tmp_path: Path):
    store = RoutingPolicyStore(tmp_path / "policy.jsonl")
    assert store.set_route(role="verifier", provider="deepseek", model="deepseek-chat", reason="cheap checks", env=_ENV).stored
    assert store.release_route(role="verifier", reason="verified share fell to 0.4 over 12 runs").stored
    assert store.resolve("verifier") is None
    assert store.history("verifier")[-1]["active"] is False


class _LLM:
    def __init__(self, provider, model):
        self.provider, self.model = provider, model

    def complete(self, **kwargs):
        return "ok"


def _router(tmp_path: Path, monkeypatch, store):
    from core.model_router import ModelRole, ModelRoute, ModelRouter

    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.setenv(name, "present")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return ModelRouter(
        default_provider="openai", default_model="gpt-default",
        routes={ModelRole.PLANNER: ModelRoute(role="planner", provider="openai", model="gpt-5.6-sol", reason="env:AGENT_PLANNER")},
        llm_factory=_LLM, routing_policy=store,
    )


def test_the_router_reads_his_decision_ahead_of_the_env_pin_and_drops_it_when_released(tmp_path, monkeypatch):
    store = RoutingPolicyStore(tmp_path / "policy.jsonl")
    router = _router(tmp_path, monkeypatch, store)
    assert router.route_for("planner").reason == "env:AGENT_PLANNER"

    verdict = store.set_route(role="planner", provider="deepseek", model="deepseek-chat", reason="cheaper, same verified share", env=dict(_ENV))
    route = router.route_for("planner")
    assert (route.provider, route.model) == ("deepseek", "deepseek-chat")
    assert route.reason == f"agent_policy:{verdict.choice.id}"
    llm = router.for_role("planner")
    assert (llm.provider, llm.model) == ("deepseek", "deepseek-chat")

    store.release_route(role="planner", reason="back to the pin")
    assert router.route_for("planner").reason == "env:AGENT_PLANNER"


def test_a_decision_on_a_provider_that_lost_its_key_falls_through_never_to_an_uncredentialed_call(tmp_path, monkeypatch):
    store = RoutingPolicyStore(tmp_path / "policy.jsonl")
    router = _router(tmp_path, monkeypatch, store)
    assert store.set_route(role="planner", provider="deepseek", model="deepseek-chat", reason="cheap", env=dict(_ENV)).stored
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    route = router.route_for("planner")
    assert route.provider == "openai" and route.reason == "env:AGENT_PLANNER"


def test_the_usage_ledger_carries_the_policy_id_and_no_secret(tmp_path, monkeypatch):
    from core.model_router import ModelRole, ModelRoute, ModelRouter
    from core.model_usage import ModelUsageLedger

    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.setenv(name, "sk-live-secret-value")
    store = RoutingPolicyStore(tmp_path / "policy.jsonl")
    verdict = store.set_route(role="synthesizer", provider="deepseek", model="deepseek-chat", reason="cost", env=dict(_ENV))
    ledger = ModelUsageLedger(path=tmp_path / "usage.jsonl")
    router = ModelRouter(
        default_provider="openai", default_model="gpt-default",
        routes={ModelRole.SYNTHESIZER: ModelRoute(role="synthesizer", provider="openai", model="gpt", reason="env:AGENT_SYNTHESIZER")},
        llm_factory=_LLM, usage_ledger=ledger, routing_policy=store,
    )
    router.for_role("synthesizer").complete(system="s", user="u", max_tokens=10, temperature=0.0)
    rows = ledger.load_records()
    assert rows and rows[-1].route_reason == f"agent_policy:{verdict.choice.id}"
    assert rows[-1].provider == "deepseek"
    assert "sk-live-secret-value" not in (tmp_path / "usage.jsonl").read_text(encoding="utf-8")
    assert "sk-live-secret-value" not in (tmp_path / "policy.jsonl").read_text(encoding="utf-8")


def test_a_new_decision_is_picked_up_without_a_restart(tmp_path, monkeypatch):
    from core.model_router import ModelRole, ModelRoute, ModelRouter
    from core.model_usage import ModelUsageLedger

    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.setenv(name, "present")
    store = RoutingPolicyStore(tmp_path / "policy.jsonl")
    ledger = ModelUsageLedger(path=tmp_path / "usage.jsonl")
    router = ModelRouter(
        default_provider="openai", default_model="gpt-default",
        routes={ModelRole.PLANNER: ModelRoute(role="planner", provider="openai", model="gpt", reason="env:AGENT_PLANNER")},
        llm_factory=_LLM, usage_ledger=ledger, routing_policy=store,
    )
    assert router.for_role("planner").provider == "openai"
    store.set_route(role="planner", provider="deepseek", model="deepseek-chat", reason="cheaper", env=dict(_ENV))
    assert router.for_role("planner").provider == "deepseek", "the cached client must be dropped when the policy moves"


def test_the_tool_needs_a_reason_refuses_secrets_names_the_pool_and_caps_itself(tmp_path, monkeypatch):
    from tools.model_route import ModelRouteTool

    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.setenv(name, "present")
    tool = ModelRouteTool(store=RoutingPolicyStore(tmp_path / "policy.jsonl"), max_changes_per_process=2)
    assert tool.risk == "reversible"
    with pytest.raises(ValueError, match="requires"):
        tool.run(role="planner", provider="deepseek", model="deepseek-chat")
    with pytest.raises(PermissionError):
        tool.run(role="planner", provider="deepseek", model="m", reason="r", api_key="x")
    out = tool.run(role="planner", provider="deepseek", model="deepseek-chat",
                   reason="planner breadth at 1 unit/1k", evidence="261 ok calls; 0 errors")
    assert out["set"] and out["route_id"] and out["route_reason_in_ledger"].startswith("agent_policy:")
    assert "deepseek" in out["pool"] and "google" not in out["pool"]
    leak = tool.run(role="verifier", provider="openai", model="gpt", reason="key sk-abcdefghijklmnopqrst")
    assert not leak["set"] and "secret" in leak["refused_by"]
    assert tool.run(role="verifier", provider="release", reason="back to defaults")["set"]
    capped = tool.run(role="memory_summary", provider="deepseek", model="deepseek-chat", reason="cheap")
    assert not capped["set"] and "ceiling" in capped["refused_by"]


def test_the_door_is_wired_and_classified_for_the_unattended_path():
    import inspect

    from app import bootstrap
    from core.autonomous_runtime import _AUTONOMOUS_GOAL_BLOCKED_TOOLS

    src = inspect.getsource(bootstrap.build_agent)
    assert "ModelRouteTool(" in src and "routing_policy=" in src
    assert "model_route" not in _AUTONOMOUS_GOAL_BLOCKED_TOOLS


def test_the_eye_shows_his_decision_under_the_routers_role_name_and_the_measured_table(tmp_path, monkeypatch):
    from core.model_roster import model_roster, model_roster_block

    store = RoutingPolicyStore(tmp_path / "policy.jsonl")
    env = {**_ENV, "AGENT_PLANNER_PROVIDER": "openai", "AGENT_PLANNER_MODEL": "gpt-5.6-sol"}
    verdict = store.set_route(role="repair_proposal", provider="deepseek", model="deepseek-chat",
                              reason="repairs are small diffs; cheap model verified 0.8 over 12 runs", env=env)
    roster = model_roster(env=env, routing_policy=store, workspace=None)
    by = {r["role"]: r for r in roster["routes"]}
    assert set(by) == {"planner", "synthesizer", "repair_proposal", "memory_summary", "verifier"}
    assert by["repair_proposal"]["source"] == "agent_policy" and by["repair_proposal"]["id"] == verdict.choice.id
    assert by["planner"]["source"] == "env_pin" and by["planner"]["model"] == "gpt-5.6-sol"
    assert by["verifier"]["source"] == "default"
    text = model_roster_block(roster)
    assert "Who answers each role now" in text
    assert f"repair_proposal: deepseek/deepseek-chat — agent_policy [{verdict.choice.id}]" in text
    assert "google-secret" not in text and "sk-secret" not in text
