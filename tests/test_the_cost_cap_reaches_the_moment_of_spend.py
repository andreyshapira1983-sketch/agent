"""MIR-116 fix witness: the campaign cost cap must reach the moment of spend.

The defect, measured live on 2026-08-21: `max_cost_units=8` finished at 63.
The cap was checked between cycles while the money left within one, so a cycle
was bounded in shape and unbounded in cost. The banked characterisation lives
in `test_a_campaign_cost_cap_is_not_a_ceiling.py`.

The fix reuses the run-envelope model that already carries `blocked_tools` and
`dry_run`: the campaign computes what the session cost counter may reach —
its current value plus the campaign's remaining budget — and enters a
`run_cost_envelope` for the cycle; `ModelUsageLedger.assert_can_start`, the
gate every model call passes BEFORE dispatch, refuses once that ceiling is
reached. So the worst overshoot shrinks from "one cycle of anything" to one
call's estimation error, and a nested run can narrow the ceiling but never
widen it.

Scope, stated so the green does not overclaim: this bounds spend that passes
through `assert_can_start`. Injected test collaborators that report invented
`cost_units_spent` never touch the gate and stay bounded only by the loop's
between-cycle check — which is exactly why the banked file's loop-level
characterisation stays true and stays in place.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.best_next_action import BestNextAction
from core.campaign import (
    CampaignActionOutcome,
    CampaignConfig,
    CampaignLedger,
    run_campaign,
)
from core.model_usage import (
    ModelBudgetExceeded,
    ModelUsageLedger,
    ModelUsageLimits,
    ModelUsageRecord,
)
from core.run_context import (
    run_cost_ceiling,
    run_cost_envelope,
    run_restrictions,
    run_scope,
)


def _spent(cost_units: int) -> ModelUsageRecord:
    return ModelUsageRecord(
        role="planner", provider="openai", model="probe", route_reason="",
        status="ok", input_tokens=1, output_tokens=1, total_tokens=2,
        cost_tier="unknown", cost_units=cost_units, estimated=False,
        started_at="", completed_at="", duration_ms=1,
    )


# --- the envelope itself -----------------------------------------------------

def test_no_ceiling_exists_outside_an_envelope() -> None:
    assert run_cost_ceiling() is None


def test_the_envelope_sets_the_ceiling_and_exit_restores() -> None:
    with run_cost_envelope(allowed_total_units=40):
        assert run_cost_ceiling() == 40
    assert run_cost_ceiling() is None


def test_a_nested_envelope_narrows_and_never_widens() -> None:
    with run_cost_envelope(allowed_total_units=40):
        with run_cost_envelope(allowed_total_units=25):
            assert run_cost_ceiling() == 25
        # attempting to WIDEN keeps the outer ceiling — the same non-liftable
        # shape as blocked_tools and dry_run
        with run_cost_envelope(allowed_total_units=1000):
            assert run_cost_ceiling() == 40
        assert run_cost_ceiling() == 40


def test_entering_a_fresh_run_identity_does_not_shed_the_ceiling() -> None:
    with run_cost_envelope(allowed_total_units=40):
        with run_scope("run-inner", "task-inner"):
            assert run_cost_ceiling() == 40
        with run_restrictions(dry_run=True):
            assert run_cost_ceiling() == 40


# --- the gate ---------------------------------------------------------------

def test_the_gate_refuses_once_the_run_ceiling_is_reached() -> None:
    """The session has already spent up to the envelope: the NEXT call must be
    refused before dispatch, not billed and regretted."""
    ledger = ModelUsageLedger(limits=ModelUsageLimits())  # own limits: all off
    ledger.records.append(_spent(8))
    with run_cost_envelope(allowed_total_units=8), pytest.raises(ModelBudgetExceeded):
        ledger.assert_can_start(role="planner", provider="openai", model="m")


def test_the_gate_lets_a_call_through_under_the_ceiling() -> None:
    ledger = ModelUsageLedger(limits=ModelUsageLimits())
    ledger.records.append(_spent(3))
    with run_cost_envelope(allowed_total_units=100):
        ledger.assert_can_start(role="planner", provider="openai", model="m")


def test_the_estimate_is_checked_before_the_money_leaves() -> None:
    """A call whose ESTIMATE would pass the ceiling is refused pre-flight: the
    envelope is a bound on the next spend, not a post-mortem."""
    ledger = ModelUsageLedger(limits=ModelUsageLimits())
    ledger.records.append(_spent(7))
    big = "x" * 40_000  # enough tokens for a nonzero cost estimate
    with run_cost_envelope(allowed_total_units=8), pytest.raises(ModelBudgetExceeded):
        ledger.assert_can_start(
            role="planner", provider="openai", model="m",
            system="", user=big, max_output_tokens=4000,
        )


def test_without_an_envelope_the_gate_behaves_exactly_as_before() -> None:
    ledger = ModelUsageLedger(limits=ModelUsageLimits())
    ledger.records.append(_spent(10_000))
    ledger.assert_can_start(role="planner", provider="openai", model="m")


# --- the campaign wires the two together ------------------------------------

def _action(name: str) -> BestNextAction:
    return BestNextAction(
        action=name, title=name, severity="medium", priority=55,
        reason="probe", risk="read_only",
    )


class _TwoActions:
    """Two distinct useful actions so the repeat-skip does not collapse them."""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self, agent, workspace, approval_inbox, goal=""):
        self.n += 1
        return {"action": _action(f"probe_action_{self.n}")}


class _CeilingProbe:
    """Records the ceiling visible at the moment the cycle executes."""

    def __init__(self, cost_per_cycle: int) -> None:
        self.cost = cost_per_cycle
        self.seen: list[int | None] = []

    def __call__(self, **_kwargs) -> CampaignActionOutcome:
        self.seen.append(run_cost_ceiling())
        return CampaignActionOutcome(
            result="completed", llm_calls_spent=1,
            cost_units_spent=self.cost, artifact="probe",
        )


def _agent_with_session_cost(units: int):
    ledger = ModelUsageLedger(limits=ModelUsageLimits())
    if units:
        ledger.records.append(_spent(units))
    return SimpleNamespace(
        log=None, model_router=SimpleNamespace(usage_ledger=ledger),
    )


def test_the_campaign_hands_each_cycle_its_remaining_budget(workspace) -> None:
    """Cycle 1 may spend the whole cap on top of the session's current count;
    after it reports spend, cycle 2's ceiling has shrunk by exactly that spend.
    This is the wiring the live overshoot was missing."""
    agent = _agent_with_session_cost(100)
    probe = _CeilingProbe(cost_per_cycle=3)
    run_campaign(
        CampaignConfig(goal="probe", max_cycles=3, max_cost_units=8,
                       max_idle_streak=3, cycle_pause_seconds=0),
        agent=agent,
        workspace=str(workspace),
        gather_signals=_TwoActions(),
        execute_action=probe,
        ledger=CampaignLedger(),
    )
    assert probe.seen[:2] == [100 + 8, 100 + (8 - 3)], (
        f"the cycles saw ceilings {probe.seen} — the campaign budget did not "
        "reach the moment of spend"
    )


def test_an_agent_without_a_usage_ledger_still_runs_the_loop(workspace) -> None:
    """No ledger to measure the session by → no envelope, and the loop's
    between-cycle check keeps working as before. The fix must not make a
    campaign impossible for a host that cannot count."""
    probe = _CeilingProbe(cost_per_cycle=63)
    result = run_campaign(
        CampaignConfig(goal="probe", max_cycles=5, max_cost_units=8,
                       max_idle_streak=3, cycle_pause_seconds=0),
        agent=SimpleNamespace(log=None),
        workspace=str(workspace),
        gather_signals=_TwoActions(),
        execute_action=probe,
        ledger=CampaignLedger(),
    )
    assert probe.seen == [None]
    assert result.status == "stopped"
    assert "cost_units=63/8" in result.stop_reason
