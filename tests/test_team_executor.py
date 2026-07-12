from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.subagent_contract import CanonicalSubagentContract
from core.subagent_contract_audit import (
    ContractAuditIssue,
    ContractAuditReport,
    SubagentExecutionReceipt,
)
from core.subagent_registry import SubagentRegistry
from core.subagent_runner import SubagentContractRefused
from core.team_executor import TeamBudget, TeamExecutor
from core.team_plan import SubagentContract, TeamPlan, TeamPlanner


class _RecordingRunner:
    def __init__(self) -> None:
        self.contracts: list[CanonicalSubagentContract] = []

    def run_contract(self, contract: CanonicalSubagentContract):
        self.contracts.append(contract)
        return SimpleNamespace(
            answer="canonical result",
            confidence_score=0.75,
            quality_score=4,
        )


class _RefusingRunner:
    def run_contract(self, contract: CanonicalSubagentContract):
        raise ValueError(f"refused {contract.contract_id}")


class _PolicyRefusingRunner:
    def run_contract(self, contract: CanonicalSubagentContract):
        raise SubagentContractRefused(f"policy refused {contract.contract_id}")


class _ErrorResultRunner:
    def run_contract(self, contract: CanonicalSubagentContract):
        receipt = SubagentExecutionReceipt(
            contract.contract_id,
            contract.schema_version,
            used_tools=("file_read",),
            iterations=1,
        )
        return SimpleNamespace(
            status="error",
            error="child crashed",
            answer="",
            confidence_score=0.0,
            quality_score=0,
        )


class _AuditedRunner:
    def run_contract(self, contract: CanonicalSubagentContract):
        receipt = SubagentExecutionReceipt(
            contract.contract_id,
            contract.schema_version,
            used_tools=("file_read",),
            iterations=1,
        )
        return SimpleNamespace(
            status="success",
            answer="audited result",
            confidence_score=0.8,
            quality_score=4,
            execution_receipt=receipt,
            contract_audit=ContractAuditReport(
                contract.contract_id,
                contract.schema_version,
                "unknown",
                (
                    ContractAuditIssue(
                        "model_calls_unmeasured",
                        "unknown",
                        "model calls were not measured",
                    ),
                ),
            ),
        )


class _ContractRegistry:
    def __init__(self) -> None:
        self.events: list[tuple[CanonicalSubagentContract, str]] = []

    def record_contract_run(
        self,
        contract: CanonicalSubagentContract,
        outcome: str,
    ) -> bool:
        self.events.append((contract, outcome))
        return True


class _BoomRegistry:
    def record_contract_run(
        self,
        contract: CanonicalSubagentContract,
        outcome: str,
    ) -> bool:
        raise OSError("disk full")


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


def test_team_executor_rejects_non_dry_run_mode_without_runner():
    plan = TeamPlanner().plan("news business architecture")

    with pytest.raises(ValueError, match="SubAgentRunner"):
        TeamExecutor().run(plan, dry_run=False)


def test_team_executor_uses_canonical_runner_boundary_for_real_execution():
    contract = SubagentContract(
        name="AuditAgent",
        role="auditor",
        objective="audit one component",
        inputs=("repo",),
        outputs=("findings",),
        allowed_tools=("file_read",),
        model_role="verifier",
        max_iterations=1,
        max_model_calls=2,
        max_cost_units=3,
    )
    plan = TeamPlan(
        goal="audit",
        needed=True,
        reasoning="test",
        contracts=(contract,),
        total_model_calls=2,
        total_cost_units=3,
    )
    runner = _RecordingRunner()
    registry = _ContractRegistry()

    report = TeamExecutor(runner=runner, registry=registry).run(plan, dry_run=False)

    assert report.status == "completed"
    assert report.steps[0].status == "executed"
    assert report.steps[0].answer == "canonical result"
    assert len(runner.contracts) == 1
    canonical = runner.contracts[0]
    assert canonical.source == "team_plan"
    assert canonical.name == "AuditAgent"
    assert canonical.tool_scope.allowed_tools == ("file_read",)
    assert canonical.model_role == "verifier"
    assert registry.events == [(canonical, "executed")]


def test_team_executor_reports_canonical_runner_refusal_as_error():
    contract = SubagentContract(
        name="RefusedAgent",
        role="worker",
        objective="unsupported work",
        inputs=("goal",),
        outputs=("report",),
        max_iterations=2,
    )
    plan = TeamPlan(
        goal="unsupported",
        needed=True,
        reasoning="test",
        contracts=(contract,),
    )

    registry = _ContractRegistry()
    report = TeamExecutor(runner=_RefusingRunner(), registry=registry).run(
        plan,
        dry_run=False,
    )

    assert report.status == "blocked"
    assert report.steps[0].status == "error"
    assert "ValueError: refused subc_" in report.steps[0].summary
    assert any("RefusedAgent failed" in warning for warning in report.warnings)
    assert registry.events[0][1] == "error"


def test_policy_refusal_is_recorded_separately_from_runtime_error():
    contract = SubagentContract(
        name="PolicyAgent",
        role="worker",
        objective="policy-blocked work",
        inputs=("goal",),
        outputs=("report",),
    )
    plan = TeamPlan(goal="policy", needed=True, reasoning="test", contracts=(contract,))
    registry = _ContractRegistry()

    report = TeamExecutor(runner=_PolicyRefusingRunner(), registry=registry).run(
        plan,
        dry_run=False,
    )

    assert report.status == "blocked"
    assert registry.events[0][1] == "refused"


def test_error_result_is_not_reported_as_executed_success():
    contract = SubagentContract(
        name="ErrorAgent",
        role="worker",
        objective="failing work",
        inputs=("goal",),
        outputs=("report",),
    )
    plan = TeamPlan(goal="error", needed=True, reasoning="test", contracts=(contract,))
    registry = _ContractRegistry()

    report = TeamExecutor(runner=_ErrorResultRunner(), registry=registry).run(
        plan,
        dry_run=False,
    )

    assert report.status == "blocked"
    assert report.steps[0].status == "error"
    assert "child crashed" in report.steps[0].summary
    assert registry.events[0][1] == "error"


def test_registry_write_failure_warns_but_does_not_change_success():
    contract = SubagentContract(
        name="AuditAgent",
        role="auditor",
        objective="audit",
        inputs=("repo",),
        outputs=("findings",),
    )
    plan = TeamPlan(goal="audit", needed=True, reasoning="test", contracts=(contract,))

    report = TeamExecutor(
        runner=_RecordingRunner(),
        registry=_BoomRegistry(),
    ).run(plan, dry_run=False)

    assert report.status == "completed"
    assert report.steps[0].status == "executed"
    assert any("registry write failed" in warning for warning in report.warnings)


def test_non_execution_paths_never_write_contract_registry():
    contract = SubagentContract(
        name="AuditAgent",
        role="auditor",
        objective="audit",
        inputs=("repo",),
        outputs=("findings",),
    )
    plan = TeamPlan(goal="audit", needed=True, reasoning="test", contracts=(contract,))
    registry = _ContractRegistry()
    executor = TeamExecutor(runner=_RecordingRunner(), registry=registry)

    executor.run(plan, dry_run=True)
    executor.run(
        TeamPlan(
            goal="approval",
            needed=True,
            reasoning="test",
            contracts=(
                SubagentContract(
                    name="ApprovalAgent",
                    role="worker",
                    objective="wait",
                    inputs=("goal",),
                    outputs=("report",),
                    approval_required=True,
                ),
            ),
        ),
        dry_run=False,
    )
    executor.run(
        plan,
        dry_run=False,
        budget=TeamBudget(max_model_calls=0, max_cost_units=0),
    )

    assert registry.events == []


def test_team_executor_persists_outcome_in_real_registry(tmp_path):
    contract = SubagentContract(
        name="AuditAgent",
        role="auditor",
        objective="audit",
        inputs=("repo",),
        outputs=("findings",),
    )
    plan = TeamPlan(goal="audit", needed=True, reasoning="test", contracts=(contract,))
    registry = SubagentRegistry.load(tmp_path)

    report = TeamExecutor(
        runner=_AuditedRunner(),
        registry=registry,
    ).run(plan, dry_run=False)

    assert report.status == "completed"
    reloaded = SubagentRegistry.load(tmp_path)
    assert len(reloaded.contract_runs) == 1
    record = next(iter(reloaded.contract_runs.values()))
    assert record.role_id == "auditor"
    assert record.executed == 1
    assert record.audit_unknown == 1
    assert record.last_execution_receipt["used_tools"] == ["file_read"]
    assert record.last_audit_report["verdict"] == "unknown"


def test_team_executor_returns_not_needed_for_simple_plan():
    plan = TeamPlanner().plan("rewrite sentence")

    report = TeamExecutor().run(plan)

    assert report.status == "not_needed"
    assert report.steps == ()
    assert "not needed" in report.warnings[0]
