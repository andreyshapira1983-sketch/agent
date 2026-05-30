from __future__ import annotations

import pytest

from core.team_plan import SubagentContract, TeamPlanner


def test_team_planner_skips_simple_tasks():
    plan = TeamPlanner().plan("rewrite this sentence")

    assert plan.needed is False
    assert plan.contracts == ()
    assert "not needed" in plan.user_summary()


def test_team_planner_creates_bounded_contracts_for_multi_concern_goal():
    plan = TeamPlanner().plan(
        "Build an AI news and business opportunity radar for agent architecture"
    )
    names = [contract.name for contract in plan.contracts]

    assert plan.needed is True
    assert "BudgetWatchAgent" in names
    assert "NewsSignalAgent" in names
    assert "BusinessOpportunityAgent" in names
    assert "ArchitectureImpactAgent" in names
    assert "VerifierAgent" in names
    assert plan.total_model_calls == sum(c.max_model_calls for c in plan.contracts)
    assert plan.total_cost_units == sum(c.max_cost_units for c in plan.contracts)
    assert all(c.max_model_calls >= 1 for c in plan.contracts)


def test_team_planner_limit_truncates_and_warns():
    plan = TeamPlanner().plan(
        "news business code architecture budget verification agent plan",
        limit=2,
    )

    assert len(plan.contracts) == 2
    assert "team plan was truncated by --limit" in plan.warnings


def test_subagent_contract_rejects_unknown_or_overlapping_tools():
    with pytest.raises(ValueError):
        SubagentContract(
            name="BadAgent",
            role="bad",
            objective="bad",
            inputs=("goal",),
            outputs=("result",),
            allowed_tools=("unknown_tool",),
        )

    with pytest.raises(ValueError):
        SubagentContract(
            name="OverlapAgent",
            role="bad",
            objective="bad",
            inputs=("goal",),
            outputs=("result",),
            allowed_tools=("file_read",),
            forbidden_tools=("file_read",),
        )


def test_team_plan_json_shape_is_stable():
    plan = TeamPlanner().plan("news and architecture signals for my agent")
    payload = plan.to_dict()

    assert payload["needed"] is True
    assert isinstance(payload["contracts"], list)
    assert {"name", "role", "objective", "allowed_tools", "max_model_calls"} <= set(
        payload["contracts"][0]
    )
