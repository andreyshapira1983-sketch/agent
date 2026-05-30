from __future__ import annotations

import pytest

from core.team_executor import TeamBudget, TeamExecutor
from core.team_plan import SubagentContract, TeamPlan, TeamPlanner


def test_team_executor_walks_contracts_and_builds_verifier_handoff():
    plan = TeamPlanner().plan(
        "AI news business code repair source verification architecture roadmap",
        limit=5,
    )

    report = TeamExecutor().run(plan, budget=TeamBudget(max_model_calls=20, max_cost_units=30))
    payload = report.to_dict()

    assert payload["dry_run"] is True
    assert payload["status"] == "completed"
    assert payload["used_model_calls"] == sum(c.max_model_calls for c in plan.contracts)
    assert payload["used_cost_units"] == sum(c.max_cost_units for c in plan.contracts)
    assert [step["order"] for step in payload["steps"]] == list(
        range(1, len(plan.contracts) + 1)
    )
    assert all(step["status"] == "dry_run_planned" for step in payload["steps"])
    assert payload["verifier_handoffs"]
    assert any(
        handoff["verifier"] == "VerifierAgent"
        for handoff in payload["verifier_handoffs"]
    )


def test_team_executor_stops_before_contract_that_would_exhaust_budget():
    plan = TeamPlanner().plan("news business architecture agent", limit=5)

    report = TeamExecutor().run(plan, budget=TeamBudget(max_model_calls=1, max_cost_units=1))
    payload = report.to_dict()

    assert payload["status"] == "budget_exhausted"
    assert payload["stop_reason"].startswith("team budget exhausted before")
    assert payload["steps"][0]["status"] == "dry_run_planned"
    assert payload["steps"][1]["status"] == "budget_blocked"
    assert payload["steps"][1]["reserved_model_calls"] == 0


def test_team_executor_marks_approval_required_contracts_without_running_them():
    plan = TeamPlan(
        goal="dangerous delegation",
        needed=True,
        reasoning="test",
        contracts=(
            SubagentContract(
                name="ApprovalAgent",
                role="effectful_worker",
                objective="would need approval",
                inputs=("goal",),
                outputs=("report",),
                allowed_tools=("file_read",),
                approval_required=True,
            ),
        ),
        total_model_calls=1,
        total_cost_units=3,
    )

    report = TeamExecutor().run(plan)

    assert report.status == "blocked"
    assert report.steps[0].status == "approval_required"
    assert any("ApprovalAgent requires approval" in warning for warning in report.warnings)


def test_team_executor_rejects_non_dry_run_mode():
    plan = TeamPlanner().plan("news business architecture")

    with pytest.raises(ValueError):
        TeamExecutor().run(plan, dry_run=False)


def test_team_executor_returns_not_needed_for_simple_plan():
    plan = TeamPlanner().plan("rewrite sentence")

    report = TeamExecutor().run(plan)

    assert report.status == "not_needed"
    assert report.steps == ()
    assert "not needed" in report.warnings[0]
