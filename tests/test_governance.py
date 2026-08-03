"""Tests for governance modes over autonomous learning/repair/evolution."""

from __future__ import annotations

import pytest

from core.governance import AgentMode, GovernedOperation, GovernancePolicy


def test_diagnostic_mode_allows_read_only_surfaces():
    policy = GovernancePolicy()
    for op in (
        GovernedOperation.READ_LOGS,
        GovernedOperation.RUN_TESTS,
        GovernedOperation.READ_SOURCE,
        GovernedOperation.FETCH_WEB,
    ):
        decision = policy.evaluate(mode=AgentMode.DIAGNOSTIC, operation=op)
        assert decision.allowed


def test_diagnostic_mode_denies_writes():
    decision = GovernancePolicy().evaluate(
        mode=AgentMode.DIAGNOSTIC,
        operation=GovernedOperation.APPLY_CODE_CHANGE,
    )
    assert decision.denied
    assert "inspect only" in " ".join(decision.reasons)


def test_learning_mode_allows_verified_memory_write():
    decision = GovernancePolicy().evaluate(
        mode=AgentMode.LEARNING,
        operation=GovernedOperation.WRITE_MEMORY,
        evidence_verified=True,
    )
    assert decision.allowed


def test_learning_mode_requires_approval_for_unverified_memory():
    decision = GovernancePolicy().evaluate(
        mode=AgentMode.LEARNING,
        operation=GovernedOperation.WRITE_MEMORY,
        evidence_verified=False,
    )
    assert decision.requires_approval


def test_learning_mode_denies_code_change():
    decision = GovernancePolicy().evaluate(
        mode=AgentMode.LEARNING,
        operation=GovernedOperation.APPLY_CODE_CHANGE,
        evidence_verified=True,
    )
    assert decision.denied


def test_repair_mode_can_propose_diff_after_verified_diagnosis():
    decision = GovernancePolicy().evaluate(
        mode=AgentMode.REPAIR,
        operation=GovernedOperation.PROPOSE_DIFF,
        evidence_verified=True,
    )
    assert decision.allowed


def test_repair_write_requires_verified_diagnosis_rollback_and_approval():
    denied = GovernancePolicy().evaluate(
        mode=AgentMode.REPAIR,
        operation=GovernedOperation.APPLY_CODE_CHANGE,
        evidence_verified=True,
        has_rollback=False,
    )
    assert denied.denied

    gated = GovernancePolicy().evaluate(
        mode=AgentMode.REPAIR,
        operation=GovernedOperation.APPLY_CODE_CHANGE,
        evidence_verified=True,
        has_rollback=True,
    )
    assert gated.requires_approval


def test_repair_rollback_allowed_only_with_compensation_plan():
    policy = GovernancePolicy()
    assert policy.evaluate(
        mode=AgentMode.REPAIR,
        operation=GovernedOperation.ROLLBACK,
        has_rollback=False,
    ).denied
    assert policy.evaluate(
        mode=AgentMode.REPAIR,
        operation=GovernedOperation.ROLLBACK,
        has_rollback=True,
    ).allowed


def test_improvement_write_requires_evidence_tests_rollback_and_approval():
    policy = GovernancePolicy()
    denied = policy.evaluate(
        mode=AgentMode.IMPROVEMENT,
        operation=GovernedOperation.ADD_TOOL,
        evidence_verified=True,
        tests_passed=False,
        has_rollback=True,
    )
    assert denied.denied

    gated = policy.evaluate(
        mode=AgentMode.IMPROVEMENT,
        operation=GovernedOperation.ADD_TOOL,
        evidence_verified=True,
        tests_passed=True,
        has_rollback=True,
    )
    assert gated.requires_approval


def test_governance_mode_approval_gates_policy_and_channels():
    policy = GovernancePolicy()
    for op in (
        GovernedOperation.CHANGE_POLICY,
        GovernedOperation.ENABLE_EXTERNAL_CHANNEL,
    ):
        decision = policy.evaluate(mode=AgentMode.GOVERNANCE, operation=op)
        assert decision.requires_approval


def test_shell_is_approval_gated_in_repair_and_improvement():
    policy = GovernancePolicy()
    assert policy.evaluate(
        mode=AgentMode.REPAIR,
        operation=GovernedOperation.RUN_SHELL,
    ).requires_approval
    assert policy.evaluate(
        mode=AgentMode.IMPROVEMENT,
        operation=GovernedOperation.RUN_SHELL,
    ).requires_approval


def test_decision_serialises_to_dict():
    decision = GovernancePolicy().evaluate(
        mode="learning",
        operation="write_memory",
        evidence_verified=True,
    )
    assert decision.to_dict() == {
        "mode": "learning",
        "operation": "write_memory",
        "verdict": "allow",
        "reasons": ["verified knowledge may be saved"],
    }


def test_unknown_mode_or_operation_is_rejected():
    policy = GovernancePolicy()
    with pytest.raises(ValueError):
        policy.evaluate(mode="unknown", operation="read_logs")
    with pytest.raises(ValueError):
        policy.evaluate(mode="learning", operation="unknown")
