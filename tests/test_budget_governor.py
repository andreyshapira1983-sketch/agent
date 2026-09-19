from __future__ import annotations

from core.budget_governor import BudgetGovernor, BudgetLimits


def test_budget_reserve_allows_until_limit():
    governor = BudgetGovernor(BudgetLimits(max_cycles=2))

    first = governor.reserve("cycles", reason="one")
    second = governor.reserve("cycles", reason="two")

    assert first.allowed
    assert second.allowed
    assert governor.snapshot()["used"]["cycles"] == 2


def test_unlimited_budget_decision_payload_is_explicit():
    governor = BudgetGovernor(BudgetLimits(max_agent_runs=0))

    decision = governor.reserve("agent_runs", reason="goal task")
    payload = decision.to_dict()

    assert decision.allowed
    assert payload["limit"] == 0
    assert payload["limit_label"] == "unlimited"
    assert payload["limit_enforced"] is False


def test_enforced_budget_decision_payload_is_explicit():
    governor = BudgetGovernor(BudgetLimits(max_cycles=1))

    decision = governor.reserve("cycles", reason="one")
    payload = decision.to_dict()

    assert decision.allowed
    assert payload["limit"] == 1
    assert payload["limit_label"] == "1"
    assert payload["limit_enforced"] is True


def test_budget_denies_over_limit_without_incrementing():
    governor = BudgetGovernor(BudgetLimits(max_test_runs=1))

    assert governor.reserve("test_runs").allowed
    denied = governor.reserve("test_runs", reason="too many tests")

    assert not denied.allowed
    assert denied.used == 1
    snap = governor.snapshot()
    assert snap["used"]["test_runs"] == 1
    assert snap["denials"][0]["counter"] == "test_runs"


def test_budget_rejects_zero_amount():
    governor = BudgetGovernor()

    try:
        governor.reserve("cycles", amount=0)
    except ValueError as exc:
        assert "amount" in str(exc)
    else:
        raise AssertionError("expected ValueError")
