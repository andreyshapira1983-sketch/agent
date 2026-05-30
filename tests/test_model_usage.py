from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.model_router import ModelRole, ModelRouter
from core.model_usage import ModelBudgetExceeded, ModelUsageLedger, ModelUsageLimits
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
    assert json.loads(lines[0])["role"] == "synthesizer"


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
