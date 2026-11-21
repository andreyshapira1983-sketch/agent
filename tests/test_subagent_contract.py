"""Hermetic tests for the canonical subagent contract bridge."""
from __future__ import annotations

import json

import pytest

from core.subagent_contract import (
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


# --- model_role validation -------------------------------------------------
# `model_role` decides which model answers. It is the only free string on this
# contract that another subsystem later resolves, and until now nothing checked
# it, while `risk_level` right beside it was checked against a closed set.


def _canonical(**overrides) -> CanonicalSubagentContract:
    fields = {
        "contract_id": "c_model_role",
        "source": "proposal",
        "name": "Auditor",
        "role": "RepositoryAuditor",
        "objective": "Audit the repository",
        "outputs": ("report",),
        "tool_scope": CanonicalToolScope(allowed_tools=("file_read",)),
        "budget_scope": CanonicalBudgetScope(max_model_calls=1, max_iterations=1),
        "risk_level": "low",
        "approval_required": False,
    }
    fields.update(overrides)
    return CanonicalSubagentContract(**fields)


def test_the_enum_member_name_is_not_a_model_role() -> None:
    # ModelRole.PLANNER.name is "PLANNER" while its value is "planner".
    with pytest.raises(ValueError, match="model_role"):
        _canonical(model_role="PLANNER")


def test_a_misspelled_model_role_is_refused_when_it_arrives_as_json() -> None:
    # This is the path a planner model actually writes.
    payload = approval_payload_from_proposal(_proposal())
    payload["canonical_contract"]["model_role"] = "sinthesizer"

    with pytest.raises(ValueError, match="model_role"):
        canonical_from_approval_payload(payload)


def test_a_blank_model_role_is_refused_rather_than_read_as_absent() -> None:
    # "" is a legal "no preference" because subagent_runner reads it with
    # `or "synthesizer"`. "   " is not: it is truthy, so the runner forwards it
    # and the router's _coerce_role raises "model role must be non-empty".
    # Letting it through here would only move that crash further from its cause.
    with pytest.raises(ValueError, match="model_role"):
        _canonical(model_role="   ")


def test_the_fixture_source_is_one_the_deserializer_accepts() -> None:
    # ContractSource is a Literal the dataclass does not enforce, so a fixture
    # is free to describe a contract from_dict would refuse. Keep them honest.
    payload = json.loads(json.dumps(_canonical().to_dict()))

    assert CanonicalSubagentContract.from_dict(payload).source == "proposal"
