from __future__ import annotations

import pytest

from core.model_router import ModelRole
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


# --- model_role validation -------------------------------------------------
# This contract already refuses unknown *tools* against a closed set. The role
# that picks the model was the one free string left unchecked.


def test_a_misspelled_model_role_is_refused_at_construction():
    with pytest.raises(ValueError, match="model_role"):
        SubagentContract(
            name="Writer",
            role="writer",
            objective="write",
            inputs=(),
            outputs=(),
            model_role="synthesiser",
        )


def test_every_real_model_role_is_accepted():
    for role in ModelRole:
        contract = SubagentContract(
            name="Writer",
            role="writer",
            objective="write",
            inputs=(),
            outputs=(),
            model_role=role.value,
        )
        assert contract.model_role == role.value


def test_the_built_in_team_templates_all_name_a_real_model_role():
    # Guards the fix against itself: the planner ships contracts of its own.
    plan = TeamPlanner().plan(
        "Build an AI news and business opportunity radar for agent architecture"
    )

    assert plan.contracts
    known = {role.value for role in ModelRole}
    assert {c.model_role for c in plan.contracts} <= known
