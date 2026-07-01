from __future__ import annotations

from pathlib import Path

import pytest

from core.budget_ledger import BudgetLedger, BudgetWindow
from core.model_router import ModelRole, ModelRouter
from core.model_usage import ModelBudgetExceeded, ModelUsageLedger, ModelUsageLimits
from core.state_integrity import decode_state_row
from tests.conftest import FakeLLM


class UsageLLM(FakeLLM):
    provider = "fake"
    model = "fake-1"

    def __init__(self, response: str = "ok"):
        super().__init__([response])
        self.last_usage = {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}

    def complete(self, *args, **kwargs) -> str:
        result = super().complete(*args, **kwargs)
        self.last_usage = {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
        return result


def test_model_usage_ledger_records_role_model_tokens_and_file(tmp_path: Path):
    ledger = ModelUsageLedger(path=tmp_path / "usage.jsonl")

    def factory(provider: str | None, model: str | None) -> UsageLLM:
        llm = UsageLLM("hello")
        llm.provider = provider or "fake"
        llm.model = model or "fake-1"
        return llm

    router = ModelRouter(
        default_provider="fake",
        default_model="fake-1",
        llm_factory=factory,
        usage_ledger=ledger,
    )

    answer = router.for_role(ModelRole.SYNTHESIZER).complete(
        system="system",
        user="user",
    )
    snapshot = router.usage_snapshot()

    assert answer == "hello"
    assert snapshot is not None
    assert snapshot["totals"]["calls"] == 1
    assert snapshot["totals"]["total_tokens"] == 18
    assert snapshot["by_role"]["synthesizer"]["calls"] == 1
    assert snapshot["by_model"]["fake/fake-1"]["calls"] == 1
    lines = (tmp_path / "usage.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert decode_state_row(lines[0])["role"] == "synthesizer"


def test_model_usage_ledger_blocks_when_call_budget_exhausted(tmp_path: Path):
    ledger = ModelUsageLedger(
        path=tmp_path / "usage.jsonl",
        limits=ModelUsageLimits(max_calls=1),
    )
    llm = UsageLLM("ok")
    router = ModelRouter(
        default_provider="fake",
        default_model="fake-1",
        llm_factory=lambda provider, model: llm,
        usage_ledger=ledger,
    )

    router.for_role(ModelRole.PLANNER).complete(system="s", user="u")
    with pytest.raises(ModelBudgetExceeded):
        router.for_role(ModelRole.SYNTHESIZER).complete(system="s", user="u")

    snapshot = router.usage_snapshot()
    assert snapshot is not None
    assert snapshot["totals"]["calls"] == 1


def test_model_usage_limits_ignore_historical_jsonl_records(tmp_path: Path):
    path = tmp_path / "usage.jsonl"
    old_ledger = ModelUsageLedger(path=path)
    old_ledger.record(
        role="planner",
        provider="fake",
        model="old",
        route_reason="test",
        cost_tier="low",
        status="success",
        input_tokens=1,
        output_tokens=1,
        estimated=True,
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:00+00:00",
        duration_ms=0,
    )
    ledger = ModelUsageLedger(path=path, limits=ModelUsageLimits(max_calls=1))
    llm = UsageLLM("ok")
    router = ModelRouter(
        default_provider="fake",
        default_model="fake-1",
        llm_factory=lambda provider, model: llm,
        usage_ledger=ledger,
    )

    router.for_role(ModelRole.SYNTHESIZER).complete(system="s", user="u")
    with pytest.raises(ModelBudgetExceeded):
        router.for_role(ModelRole.PLANNER).complete(system="s", user="u")

    snapshot = router.usage_snapshot()
    assert snapshot is not None
    assert snapshot["totals"]["calls"] == 2
    assert snapshot["session_totals"]["calls"] == 1


def test_model_usage_ledger_estimates_tokens_when_provider_has_no_usage(tmp_path: Path):
    ledger = ModelUsageLedger(path=tmp_path / "usage.jsonl")
    llm = FakeLLM(["abcd"])
    router = ModelRouter(
        default_provider="fake",
        default_model="fake-1",
        llm_factory=lambda provider, model: llm,
        usage_ledger=ledger,
    )

    router.for_role(ModelRole.MEMORY_SUMMARY).complete(system="abcd", user="abcdefgh")
    snapshot = router.usage_snapshot()

    assert snapshot is not None
    assert snapshot["totals"]["calls"] == 1
    assert snapshot["recent"][0]["estimated"] is True
    assert snapshot["totals"]["total_tokens"] > 0


def test_model_usage_ledger_enforces_persistent_call_window_across_sessions(tmp_path: Path):
    budget_path = tmp_path / "budget.jsonl"
    usage_path = tmp_path / "usage.jsonl"
    first_budget = BudgetLedger(
        path=budget_path,
        windows=(BudgetWindow("day", 86400, {"llm_calls": 1}),),
    )
    first_ledger = ModelUsageLedger(
        path=usage_path,
        budget_ledger=first_budget,
    )
    router = ModelRouter(
        default_provider="fake",
        default_model="fake-1",
        llm_factory=lambda provider, model: UsageLLM("ok"),
        usage_ledger=first_ledger,
    )
    router.for_role(ModelRole.PLANNER).complete(system="s", user="u")

    second_budget = BudgetLedger(
        path=budget_path,
        windows=(BudgetWindow("day", 86400, {"llm_calls": 1}),),
    )
    second_ledger = ModelUsageLedger(
        path=usage_path,
        budget_ledger=second_budget,
    )
    second_router = ModelRouter(
        default_provider="fake",
        default_model="fake-1",
        llm_factory=lambda provider, model: UsageLLM("ok"),
        usage_ledger=second_ledger,
    )

    with pytest.raises(ModelBudgetExceeded, match="persistent day model call budget"):
        second_router.for_role(ModelRole.SYNTHESIZER).complete(system="s", user="u")


def test_model_usage_records_tokens_and_cost_units_to_persistent_budget(tmp_path: Path):
    budget = BudgetLedger(
        path=tmp_path / "budget.jsonl",
        windows=(BudgetWindow("day", 86400, {"model_tokens": 100_000, "model_cost_units": 10_000}),),
    )
    ledger = ModelUsageLedger(path=tmp_path / "usage.jsonl", budget_ledger=budget)
    router = ModelRouter(
        default_provider="fake",
        default_model="fake-1",
        llm_factory=lambda provider, model: UsageLLM("ok"),
        usage_ledger=ledger,
    )

    router.for_role(ModelRole.SYNTHESIZER).complete(system="s", user="u")
    snapshot = budget.snapshot()

    assert snapshot["totals"]["model_tokens"] == 18
    assert snapshot["totals"]["model_cost_units"] >= 1


class ExplodingLLM(FakeLLM):
    provider = "fake"
    model = "fake-1"

    def __init__(self):
        super().__init__(["should-not-run"])
        self.completed = False

    def complete(self, *args, **kwargs) -> str:
        self.completed = True
        raise AssertionError("LLM.complete must not run when pre-flight blocks")


def test_assert_can_start_blocks_oversized_call_on_session_cost_cap():
    ledger = ModelUsageLedger(limits=ModelUsageLimits(max_cost_units=5))

    # A large output cap on the (unknown-tier) call estimates well above 5 units.
    with pytest.raises(ModelBudgetExceeded, match="model cost budget would exhaust"):
        ledger.assert_can_start(
            role="synthesizer",
            provider="fake",
            model="fake-1",
            system="s",
            user="u",
            max_output_tokens=4096,
            cost_tier="unknown",
        )
    # Nothing recorded — the phantom call never happened.
    assert ledger.snapshot()["totals"]["calls"] == 0


def test_assert_can_start_blocks_oversized_call_on_session_token_cap():
    ledger = ModelUsageLedger(limits=ModelUsageLimits(max_tokens=100))

    with pytest.raises(ModelBudgetExceeded, match="model token budget would exhaust"):
        ledger.assert_can_start(
            role="planner",
            provider="fake",
            model="fake-1",
            system="s",
            user="u",
            max_output_tokens=2048,
            cost_tier="unknown",
        )


def test_assert_can_start_allows_small_call_within_caps():
    ledger = ModelUsageLedger(limits=ModelUsageLimits(max_tokens=10_000, max_cost_units=1_000))

    # Should not raise for a modest estimate that fits inside the caps.
    ledger.assert_can_start(
        role="synthesizer",
        provider="fake",
        model="fake-1",
        system="hi",
        user="there",
        max_output_tokens=100,
        cost_tier="low",
    )


def test_assert_can_start_preflight_persistent_window_no_phantom_call(tmp_path: Path):
    budget = BudgetLedger(
        path=tmp_path / "budget.jsonl",
        windows=(BudgetWindow("day", 86400, {"model_cost_units": 3, "llm_calls": 10}),),
    )
    ledger = ModelUsageLedger(
        path=tmp_path / "usage.jsonl",
        budget_ledger=budget,
    )

    with pytest.raises(ModelBudgetExceeded, match="persistent day model cost budget would exhaust"):
        ledger.assert_can_start(
            role="synthesizer",
            provider="fake",
            model="fake-1",
            system="s",
            user="u",
            max_output_tokens=4096,
            cost_tier="unknown",
        )

    # The blocked estimate must not consume the persistent llm_calls window.
    assert budget.snapshot()["totals"].get("llm_calls", 0) == 0


def test_router_complete_blocks_before_llm_call_when_estimate_exceeds_cap(tmp_path: Path):
    exploding = ExplodingLLM()
    ledger = ModelUsageLedger(
        path=tmp_path / "usage.jsonl",
        limits=ModelUsageLimits(max_cost_units=5),
    )
    router = ModelRouter(
        default_provider="fake",
        default_model="fake-1",
        llm_factory=lambda provider, model: exploding,
        usage_ledger=ledger,
    )

    with pytest.raises(ModelBudgetExceeded, match="would exhaust"):
        router.for_role(ModelRole.SYNTHESIZER).complete(
            system="s", user="u", max_tokens=4096
        )

    assert exploding.completed is False
    assert ledger.snapshot()["totals"]["calls"] == 0
