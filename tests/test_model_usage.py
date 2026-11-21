from __future__ import annotations

from pathlib import Path

import pytest

from core.budget_ledger import BudgetLedger, BudgetWindow
from core.model_router import ModelRole, ModelRouter
from core.model_usage import ModelBudgetExceeded, ModelUsageLedger, ModelUsageLimits
from tests.conftest import FakeLLM, StreamingFakeLLM


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


class ExplodingLLM(FakeLLM):
    provider = "fake"
    model = "fake-1"

    def __init__(self):
        super().__init__(["should-not-run"])
        self.completed = False

    def complete(self, *args, **kwargs) -> str:
        self.completed = True
        raise AssertionError("LLM.complete must not run when pre-flight blocks")


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


# ---------------------------------------------------------------------------
# The streamed path is billed exactly like the plain one.
#
# UsageTrackedLLM used to define only `complete`; `__getattr__` handed
# `stream_complete` to the raw provider LLM, so every streamed call skipped
# assert_can_start, log_start and record. Streaming is the REPL default, so
# the primary interactive mode under-billed every turn and enforced no cap at
# synthesis. Measured 2026-08-08 before these tests existed.
# ---------------------------------------------------------------------------


def _tracked(llm, ledger):
    router = ModelRouter(
        default_provider="fake",
        default_model="fake-1",
        llm_factory=lambda provider, model: llm,
        usage_ledger=ledger,
    )
    return router.for_role(ModelRole.SYNTHESIZER)


def test_stream_complete_records_usage_and_delivers_tokens(tmp_path: Path):
    llm = StreamingFakeLLM(["streamed answer body"])
    ledger = ModelUsageLedger(path=tmp_path / "usage.jsonl")
    seen: list[str] = []

    out = _tracked(llm, ledger).stream_complete(
        system="s", user="u", on_token=seen.append
    )

    assert out == "streamed answer body"
    assert "".join(seen).strip() == out, "every chunk must reach the callback"
    assert [r.role for r in ledger.records] == ["synthesizer"], (
        "the streamed call must leave a ledger record like any other call"
    )
    assert ledger.records[-1].status == "success"
    assert ledger.records[-1].total_tokens > 0


def test_stream_complete_blocks_before_streaming_when_budget_exhausted(tmp_path: Path):
    class ExplodingStreamLLM(StreamingFakeLLM):
        def stream_complete(self, *args, **kwargs) -> str:
            raise AssertionError("stream must not start when pre-flight blocks")

    llm = ExplodingStreamLLM(["nope"])
    ledger = ModelUsageLedger(
        path=tmp_path / "usage.jsonl", limits=ModelUsageLimits(max_cost_units=5)
    )

    with pytest.raises(ModelBudgetExceeded, match="would exhaust"):
        _tracked(llm, ledger).stream_complete(system="s", user="u", max_tokens=4096)

    assert ledger.snapshot()["totals"]["calls"] == 0


def test_stream_complete_records_the_error_when_the_stream_dies(tmp_path: Path):
    class DyingStreamLLM(StreamingFakeLLM):
        def stream_complete(self, *args, **kwargs) -> str:
            raise RuntimeError("connection torn mid-stream")

    llm = DyingStreamLLM(["nope"])
    ledger = ModelUsageLedger(path=tmp_path / "usage.jsonl")

    with pytest.raises(RuntimeError, match="torn mid-stream"):
        _tracked(llm, ledger).stream_complete(system="s", user="u")

    assert [r.status for r in ledger.records] == ["error"], (
        "a stream that died after the pre-flight still spent tokens; "
        "the ledger must carry the error record"
    )


def test_stream_complete_falls_back_to_billed_complete_without_provider_streaming(
    tmp_path: Path,
):
    """A provider double with no stream_complete still answers AND still bills.

    Mirrors core/llm.py:384 — providers that cannot stream fall back to
    complete. The wrapper must do the same instead of raising AttributeError,
    and the fallback call must go through its own billed path.
    """
    llm = FakeLLM(["plain answer"])  # no stream_complete at all
    ledger = ModelUsageLedger(path=tmp_path / "usage.jsonl")
    seen: list[str] = []

    out = _tracked(llm, ledger).stream_complete(
        system="s", user="u", on_token=seen.append
    )

    assert out == "plain answer"
    assert [r.role for r in ledger.records] == ["synthesizer"]


# ---------------------------------------------------------------------------
# Spend has to be attributable to a single run.
#
# The ledger file is append-only and outlives the process, so every run writes
# into the same history. Without a run identifier the records of a run can only
# be separated by eyeballing timestamps, which stops working the moment two
# runs overlap or a sub-agent runs inside a parent. The agent already mints a
# per-run identifier — the trace id — so this reuses it rather than inventing a
# second name for the same thing.
# ---------------------------------------------------------------------------


class _TraceStub:
    """Stand-in for TraceLogger: the ledger only ever reads `trace_id`."""

    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self.events: list[tuple[str, dict]] = []

    def log(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))


def _record_one(ledger: ModelUsageLedger, *, role: str = "planner"):
    return ledger.record(
        role=role,
        provider="fake",
        model="fake-1",
        route_reason="default",
        cost_tier="low",
        status="success",
        input_tokens=3,
        output_tokens=2,
        estimated=False,
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:01+00:00",
        duration_ms=1000,
    )


def test_outside_any_run_the_session_id_is_still_recorded():
    """No run scope (a REPL command, a probe) → the session id is the best id."""
    ledger = ModelUsageLedger(logger=_TraceStub("session-1"))

    _record_one(ledger)

    assert ledger.records[-1].run_id == "session-1"


def test_an_explicit_ledger_run_id_is_not_overridden_by_a_run_scope():
    """A sub-agent ledger built for one run stays pinned to it."""
    from core.run_context import run_scope

    ledger = ModelUsageLedger(logger=_TraceStub("session-1"), run_id="explicit")

    with run_scope("run-A"):
        _record_one(ledger)

    assert ledger.records[-1].run_id == "explicit"
