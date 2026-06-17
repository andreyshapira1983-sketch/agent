"""Tests for the deep/Opus escalation gate (core/deep_escalation.py)."""
from __future__ import annotations

from core.deep_escalation import (
    ACTIVE_REASONS,
    EXPECTED_OUTPUTS,
    RESERVED_REASONS,
    DeepEscalationDecision,
    DeepEscalationRequest,
    OperatorEscalation,
    evaluate_deep_escalation,
)


def _approved_request(**overrides) -> DeepEscalationRequest:
    base = dict(
        role="planner",
        reason="planner_multi_file_architecture_change",
        expected_output="architecture_tradeoff",
        deep_model_available=True,
        budget_ok=True,
        operator_approved=True,
    )
    base.update(overrides)
    return DeepEscalationRequest(**base)


class TestApprove:
    def test_valid_planner_reason_approves_deep(self) -> None:
        decision = evaluate_deep_escalation(_approved_request())
        assert decision.effective_tier == "deep"
        assert decision.gate == "approved"
        assert decision.approved is True
        assert decision.route_reason == "deep_approved:planner_multi_file_architecture_change"

    def test_operator_request_reason_approves_for_synthesizer(self) -> None:
        decision = evaluate_deep_escalation(_approved_request(
            role="synthesizer",
            reason="operator_explicitly_requested_opus",
            expected_output="final_answer_high_stakes",
        ))
        assert decision.effective_tier == "deep"
        assert decision.route_reason == "deep_approved:operator_explicitly_requested_opus"

    def test_operator_approved_covers_missing_budget(self) -> None:
        # budget_ok False but operator present → still approved.
        decision = evaluate_deep_escalation(_approved_request(
            budget_ok=False, operator_approved=True,
        ))
        assert decision.approved is True

    def test_budget_ok_alone_covers_missing_operator(self) -> None:
        decision = evaluate_deep_escalation(_approved_request(
            budget_ok=True, operator_approved=False,
        ))
        assert decision.approved is True

    def test_every_expected_output_enum_is_accepted(self) -> None:
        for expected in EXPECTED_OUTPUTS:
            decision = evaluate_deep_escalation(_approved_request(expected_output=expected))
            assert decision.approved is True

    def test_every_active_reason_is_accepted(self) -> None:
        for reason in ACTIVE_REASONS:
            decision = evaluate_deep_escalation(_approved_request(role="planner", reason=reason))
            assert decision.approved is True


class TestDowngrade:
    def test_missing_reason_downgrades(self) -> None:
        decision = evaluate_deep_escalation(_approved_request(reason=None))
        assert decision.effective_tier == "standard"
        assert decision.gate == "downgraded"
        assert decision.route_reason == "deep_downgraded:missing_reason"

    def test_invalid_reason_downgrades(self) -> None:
        decision = evaluate_deep_escalation(_approved_request(reason="better_quality"))
        assert decision.route_reason == "deep_downgraded:missing_reason"

    def test_reserved_reason_is_not_active_and_downgrades(self) -> None:
        # Reserved v2 reasons must NOT unlock deep in v1.
        for reason in RESERVED_REASONS:
            decision = evaluate_deep_escalation(_approved_request(reason=reason))
            assert decision.downgraded is True
            assert decision.route_reason == "deep_downgraded:missing_reason"

    def test_vague_expected_output_downgrades(self) -> None:
        decision = evaluate_deep_escalation(_approved_request(expected_output="make_it_better"))
        assert decision.route_reason == "deep_downgraded:vague_expected_output"

    def test_missing_expected_output_downgrades(self) -> None:
        decision = evaluate_deep_escalation(_approved_request(expected_output=None))
        assert decision.route_reason == "deep_downgraded:vague_expected_output"

    def test_no_deep_model_downgrades(self) -> None:
        decision = evaluate_deep_escalation(_approved_request(deep_model_available=False))
        assert decision.route_reason == "deep_downgraded:no_deep_model"

    def test_budget_block_downgrades(self) -> None:
        decision = evaluate_deep_escalation(_approved_request(
            budget_ok=False, operator_approved=False,
        ))
        assert decision.route_reason == "deep_downgraded:budget_block"

    def test_ineligible_role_downgrades(self) -> None:
        decision = evaluate_deep_escalation(_approved_request(role="verifier"))
        assert decision.route_reason == "deep_downgraded:role_not_eligible"

    def test_autonomous_default_downgrades(self) -> None:
        # The autonomous path passes no reason/budget/operator → always standard.
        decision = evaluate_deep_escalation(DeepEscalationRequest(role="planner"))
        assert decision.effective_tier == "standard"
        assert decision.route_reason == "deep_downgraded:missing_reason"


class TestReservedSeparation:
    def test_reserved_and_active_reasons_are_disjoint(self) -> None:
        assert ACTIVE_REASONS.isdisjoint(RESERVED_REASONS)


class TestDeterminismAndSerialisation:
    def test_decision_is_deterministic(self) -> None:
        req = _approved_request()
        first = evaluate_deep_escalation(req)
        second = evaluate_deep_escalation(req)
        assert first.to_dict() == second.to_dict()

    def test_decision_to_dict_shape(self) -> None:
        decision = evaluate_deep_escalation(_approved_request())
        assert decision.to_dict() == {
            "effective_tier": "deep",
            "gate": "approved",
            "route_reason": "deep_approved:planner_multi_file_architecture_change",
        }

    def test_request_to_dict_roundtrips_fields(self) -> None:
        req = _approved_request()
        data = req.to_dict()
        assert data["role"] == "planner"
        assert data["reason"] == "planner_multi_file_architecture_change"
        assert data["deep_model_available"] is True

    def test_operator_escalation_defaults_to_present_and_approving(self) -> None:
        esc = OperatorEscalation(reason="operator_explicitly_requested_opus",
                                 expected_output="cross_file_synthesis")
        assert esc.operator_approved is True
        assert esc.budget_ok is True
        assert esc.to_dict()["reason"] == "operator_explicitly_requested_opus"

    def test_decision_is_a_frozen_dataclass(self) -> None:
        decision = evaluate_deep_escalation(_approved_request())
        assert isinstance(decision, DeepEscalationDecision)
