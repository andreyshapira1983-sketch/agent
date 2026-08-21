"""What `max_cost_units` actually bounds, measured rather than read.

Found on a live run 2026-08-21: a campaign started with `--max-cost-units 8`
finished having spent 63. The cap did stop the campaign — and the number it
stopped at was almost eight times the number given.

The module says of itself: "Budget caps (cycles / llm_calls / cost_units) stop
the campaign BEFORE the next spend, not after." Read literally that promises
total spend never passes the cap. What the loop does is check `used >= max` at
the TOP of a cycle (`core/campaign.py:156`, `:160`) and add the cycle's spend
AFTER it returns (`:274`, `:275`). So the cap gates the next CYCLE, not the
next SPEND, and one already-started cycle can cost anything.

Measured on the live run, inside a cycle: the counters that ARE enforced are
structural — cycles 1/5, agent_runs 1/1, learning_runs 1/1. The money counter
is not: `llm_calls limit=0 limit_label=unlimited limit_enforced=False`, and no
cost counter exists inside a cycle at all. A cycle is therefore bounded in
SHAPE and unbounded in COST.

Practically, `max_cost_units=N` means "N, plus one cycle of whatever that
cycle costs". For a campaign meant to run for hours that is a footnote. For an
agent meant to be left alive it is the difference between a budget and a
suggestion, which is why this is banked rather than quietly accepted.

Nothing here proposes a fix. Bounding a cycle from inside and rewording the
contract are different decisions, and both belong to the operator.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.best_next_action import BestNextAction
from core.campaign import (
    CampaignActionOutcome,
    CampaignConfig,
    CampaignLedger,
    run_campaign,
)

_CAP = 8
_ONE_CYCLE_COST = 63          # what the live run actually spent in one cycle


def _fixed_now() -> datetime:
    return datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)


def _useful_action() -> BestNextAction:
    """A non-idle action, so `execute_action` is actually called."""
    return BestNextAction(
        action="improve_failure_to_idea_pipeline",
        title="Turn the self-improvement failure into a bounded repair",
        severity="medium",
        priority=55,
        reason="a durable self-improvement issue remains unresolved",
        risk="read_only",
    )


class _AlwaysUseful:
    def __call__(self, agent, workspace, approval_inbox):
        return {"action": _useful_action()}


class _CostlyExecute:
    """One cycle that spends far more than the whole campaign was allowed."""

    def __init__(self, cost: int) -> None:
        self.cost = cost
        self.calls = 0

    def __call__(self, **_kwargs) -> CampaignActionOutcome:
        self.calls += 1
        return CampaignActionOutcome(
            result="completed", llm_calls_spent=2, cost_units_spent=self.cost,
            artifact="probe",
        )


def _run(cap: int, cost: int, max_cycles: int = 5):
    execute = _CostlyExecute(cost)
    result = run_campaign(
        CampaignConfig(goal="probe", max_cycles=max_cycles, max_cost_units=cap,
                       max_idle_streak=5, cycle_pause_seconds=0),
        agent=SimpleNamespace(log=None),
        workspace="/tmp/ws",
        gather_signals=_AlwaysUseful(),
        execute_action=execute,
        ledger=CampaignLedger(),
        now_fn=_fixed_now,
    )
    return result, execute


def test_the_cap_does_stop_the_campaign(workspace) -> None:
    """Control, and it must stay green: the cap is not decorative. Without this
    the case below would read as 'budgets do nothing', which is false."""
    result, execute = _run(_CAP, _ONE_CYCLE_COST)
    assert execute.calls == 1, (
        f"the cap allowed {execute.calls} cycles; it is meant to stop the next one"
    )
    assert result.status == "stopped"
    assert f"cost_units={_ONE_CYCLE_COST}/{_CAP}" in result.stop_reason


def test_the_overshoot_is_exactly_one_cycle(workspace) -> None:
    """Characterisation of today's semantics: the cap gates the next cycle, so
    the total is the first cycle's cost — no matter how large that is."""
    for cost in (_ONE_CYCLE_COST, _ONE_CYCLE_COST * 100):
        result, execute = _run(_CAP, cost)
        assert execute.calls == 1
        assert result.totals["cost_units"] == cost, (
            "the campaign's total is whatever the first cycle chose to spend"
        )


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, measured 2026-08-21 on a live run and banked, not fixed: the "
        "module states that budget caps stop the campaign BEFORE the next spend, "
        "which read literally means the total never passes the cap. The check is "
        "at the top of a cycle and the spend is added after it returns, so an "
        "already-started cycle can cost anything — 63 against a cap of 8 on the "
        "live run, and unbounded in principle since no cost counter is enforced "
        "inside a cycle. Fix unprescribed: bounding a cycle from inside and "
        "rewording the contract are different decisions."
    ),
    strict=True,
)
def test_total_spend_never_passes_the_cap(workspace) -> None:
    result, _ = _run(_CAP, _ONE_CYCLE_COST)
    assert result.totals["cost_units"] <= _CAP, (
        f"the campaign spent {result.totals['cost_units']} against a cap of "
        f"{_CAP} — the cap gates the next cycle, not the next spend"
    )
