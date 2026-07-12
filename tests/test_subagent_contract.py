"""Hermetic tests for the canonical subagent contract bridge."""
from __future__ import annotations

import json

import pytest

from core.subagent_contract import (
    CONTRACT_SCHEMA_VERSION,
    CanonicalBudgetScope,
    CanonicalSubagentContract,
    CanonicalToolScope,
    approval_payload_from_proposal,
    canonical_from_approval_payload,
)
from core.subagent_memory_scope import (
    BudgetScope,
    MemoryScope,
    SubagentProposal,
    ToolScope,
)
from core.team_plan import SubagentContract


def _proposal() -> SubagentProposal:
    return SubagentProposal(
        proposal_id="sap_fixed",
        created_at="2026-07-11T00:00:00+00:00",
        task_goal="Audit the repository",
        why_needed="A separate read-only context is useful.",
        proposed_role="RepositoryAuditor",
        memory_scope=MemoryScope(
            read_tags=("project", "fact"),
            write_tags=("audit",),
            write_requires_review=True,
        ),
        tool_scope=ToolScope(
            allowed_tools=("file_read", "run_tests"),
            forbidden_tools=("file_write", "shell_exec"),
            read_only=True,
        ),
        budget_scope=BudgetScope(
            max_model_calls=4,
            max_web_fetches=2,
            max_file_writes=0,
            max_cycles=2,
        ),
        risk_level="medium",
        expected_output="Evidence-backed audit report",
        approval_required=True,
    )


def _team_contract(**overrides) -> SubagentContract:
    values = {
        "name": "RepositoryAuditor",
        "role": "auditor",
        "objective": "Audit the repository",
        "inputs": ("repo_context",),
        "outputs": ("findings", "evidence"),
        "allowed_tools": ("file_read", "run_tests"),
        "forbidden_tools": ("file_write", "shell_exec"),
        "model_role": "verifier",
        "max_iterations": 2,
        "max_model_calls": 4,
        "max_cost_units": 7,
        "verifier": "VerifierAgent",
        "stop_conditions": ("output_contract_satisfied", "budget_exhausted"),
        "risk_level": "medium",
        "approval_required": True,
    }
    values.update(overrides)
    return SubagentContract(**values)


def test_proposal_adapter_preserves_declared_policy() -> None:
    canonical = CanonicalSubagentContract.from_proposal(_proposal())

    assert canonical.schema_version == CONTRACT_SCHEMA_VERSION
    assert canonical.contract_id == "sap_fixed"
    assert canonical.source == "proposal"
    assert canonical.source_id == "sap_fixed"
    assert canonical.name == "RepositoryAuditor"
    assert canonical.role == "RepositoryAuditor"
    assert canonical.objective == "Audit the repository"
    assert canonical.outputs == ("Evidence-backed audit report",)
    assert canonical.memory_scope is not None
    assert canonical.memory_scope.read_tags == ("project", "fact")
    assert canonical.memory_scope.write_tags == ("audit",)
    assert canonical.tool_scope.allowed_tools == ("file_read", "run_tests")
    assert canonical.tool_scope.forbidden_tools == ("file_write", "shell_exec")
    assert canonical.tool_scope.read_only is True
    assert canonical.budget_scope.max_model_calls == 4
    assert canonical.budget_scope.max_iterations == 2
    assert canonical.budget_scope.max_web_fetches == 2
    assert canonical.budget_scope.max_file_writes == 0
    assert canonical.budget_scope.max_cost_units is None
    assert canonical.model_role is None
    assert canonical.verifier is None
    assert canonical.stop_conditions == ()


def test_team_adapter_preserves_policy_without_inventing_memory() -> None:
    canonical = CanonicalSubagentContract.from_team_contract(_team_contract())

    assert canonical.contract_id.startswith("subc_")
    assert canonical.source == "team_plan"
    assert canonical.source_id is None
    assert canonical.name == "RepositoryAuditor"
    assert canonical.role == "auditor"
    assert canonical.inputs == ("repo_context",)
    assert canonical.outputs == ("findings", "evidence")
    assert canonical.memory_scope is None
    assert canonical.tool_scope.read_only is None
    assert canonical.budget_scope.max_cost_units == 7
    assert canonical.budget_scope.max_web_fetches is None
    assert canonical.budget_scope.max_file_writes is None
    assert canonical.model_role == "verifier"
    assert canonical.verifier == "VerifierAgent"
    assert canonical.stop_conditions == (
        "output_contract_satisfied",
        "budget_exhausted",
    )


def test_team_contract_id_is_deterministic_and_content_addressed() -> None:
    first = CanonicalSubagentContract.from_team_contract(_team_contract())
    same = CanonicalSubagentContract.from_team_contract(_team_contract())
    changed = CanonicalSubagentContract.from_team_contract(
        _team_contract(objective="Audit another repository")
    )

    assert first.contract_id == same.contract_id
    assert first.contract_id != changed.contract_id


def test_serialization_is_json_safe_and_explicit_about_unknowns() -> None:
    canonical = CanonicalSubagentContract.from_team_contract(_team_contract())
    payload = canonical.to_dict()

    assert json.loads(json.dumps(payload)) == payload
    assert payload["schema_version"] == 1
    assert payload["memory_scope"] is None
    assert payload["tool_scope"]["read_only"] is None
    assert payload["budget_scope"]["max_web_fetches"] is None


def test_adapters_do_not_mutate_legacy_serialization() -> None:
    proposal = _proposal()
    team_contract = _team_contract()
    proposal_before = proposal.to_dict()
    team_before = team_contract.to_dict()

    CanonicalSubagentContract.from_proposal(proposal)
    CanonicalSubagentContract.from_team_contract(team_contract)

    assert proposal.to_dict() == proposal_before
    assert team_contract.to_dict() == team_before


def test_tool_scope_rejects_allow_deny_overlap() -> None:
    with pytest.raises(ValueError, match="both allowed and forbidden"):
        CanonicalToolScope(
            allowed_tools=("file_read",),
            forbidden_tools=("file_read",),
        )


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"max_model_calls": -1, "max_iterations": 1}, "max_model_calls"),
        ({"max_model_calls": 1, "max_iterations": 0}, "max_iterations"),
        (
            {"max_model_calls": 1, "max_iterations": 1, "max_cost_units": -1},
            "max_cost_units",
        ),
    ],
)
def test_budget_scope_rejects_invalid_limits(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        CanonicalBudgetScope(**kwargs)


def test_canonical_dict_round_trip_is_lossless() -> None:
    original = CanonicalSubagentContract.from_proposal(_proposal())

    restored = CanonicalSubagentContract.from_dict(original.to_dict())

    assert restored == original


def test_approval_payload_contains_canonical_and_legacy_detail() -> None:
    proposal = _proposal()

    payload = approval_payload_from_proposal(proposal)
    restored = canonical_from_approval_payload(payload)

    assert payload["payload_schema"] == "canonical_subagent_approval/v1"
    assert payload["proposal"] == proposal.to_dict()
    assert restored == CanonicalSubagentContract.from_proposal(proposal)


def test_legacy_flat_approval_payload_remains_readable() -> None:
    proposal = _proposal()

    restored = canonical_from_approval_payload(proposal.to_dict())

    assert restored.contract_id == proposal.proposal_id
    assert restored.source == "proposal"
    assert restored.memory_scope is not None
    assert restored.memory_scope.read_tags == ("project", "fact")


def test_canonical_payload_rejects_invalid_nested_types() -> None:
    payload = approval_payload_from_proposal(_proposal())
    payload["canonical_contract"]["approval_required"] = "yes"

    with pytest.raises(ValueError, match="approval_required must be a boolean"):
        canonical_from_approval_payload(payload)
