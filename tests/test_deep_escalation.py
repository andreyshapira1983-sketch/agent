"""Tests for the deep/Opus escalation gate (core/deep_escalation.py)."""
from __future__ import annotations

from core.deep_escalation import (
    DeepEscalationRequest,
    OperatorEscalation,
    evaluate_deep_escalation,
)


def _approved_request(**overrides) -> DeepEscalationRequest:
    base = {
        "role": "planner",
        "reason": "planner_multi_file_architecture_change",
        "expected_output": "architecture_tradeoff",
        "deep_model_available": True,
        "budget_ok": True,
        "operator_approved": True,
    }
    base.update(overrides)
    return DeepEscalationRequest(**base)


class TestApprove:
    def test_budget_ok_alone_covers_missing_operator(self) -> None:
        decision = evaluate_deep_escalation(_approved_request(
            budget_ok=True, operator_approved=False,
        ))
        assert decision.approved is True


class TestDowngrade:
    def test_invalid_reason_downgrades(self) -> None:
        decision = evaluate_deep_escalation(_approved_request(reason="better_quality"))
        assert decision.route_reason == "deep_downgraded:unknown_reason"


    def test_vague_expected_output_downgrades(self) -> None:
        decision = evaluate_deep_escalation(_approved_request(expected_output="make_it_better"))
        assert decision.route_reason == "deep_downgraded:vague_expected_output"


    def test_budget_block_downgrades(self) -> None:
        decision = evaluate_deep_escalation(_approved_request(
            budget_ok=False, operator_approved=False,
        ))
        assert decision.route_reason == "deep_downgraded:budget_block"

    def test_ineligible_role_downgrades(self) -> None:
        decision = evaluate_deep_escalation(_approved_request(role="verifier"))
        assert decision.route_reason == "deep_downgraded:role_not_eligible"


class TestDeterminismAndSerialisation:
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
