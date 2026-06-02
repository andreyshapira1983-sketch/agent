"""Core Data Models (§12.1) — Pydantic Literal guards + defaults.

Every model in `core/models.py` is the bus between agent domains. A typo
in a `status` value or a missing default would let bad data flow between
the planner, executor, and memory. These tests pin the guard rails.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from core.models import (
    Action,
    ApprovalDecision,
    ApprovalRequest,
    ErrorObject,
    Goal,
    MemoryRecord,
    Observation,
    Plan,
    PlanStep,
    PolicyDecision,
    Task,
    ToolCall,
    ToolResult,
)


# ============================================================
# ID factories + timestamps work and are unique
# ============================================================

class TestIdsAndTimestamps:
    def test_each_model_gets_a_prefixed_id(self):
        # Spot check across domains: each model auto-fills `id` on construct.
        g = Goal(description="x", success_criteria="y")
        t = Task(description="x")
        p = Plan(goal_id=g.id)
        step = PlanStep(plan_id=p.id, order=1, action_spec={}, expected_outcome="x")
        obs = Observation(source="cli", content={})
        act = Action(step_id=step.id, type="tool_call")
        tc = ToolCall(action_id=act.id, tool_name="x", arguments={})
        tr = ToolResult(tool_call_id=tc.id, status="success")
        pol = PolicyDecision(policy_id="P", subject="s", action="a", decision="allow")
        ar = ApprovalRequest(action_id=act.id, step_id=step.id, risk="read_only")
        ad = ApprovalDecision(request_id=ar.id, decision="approve")
        mem = MemoryRecord(content="c")
        err = ErrorObject(source="s", code="c", message="m")

        for obj, prefix in [
            (g, "goal_"), (t, "task_"), (p, "plan_"), (step, "step_"),
            (obs, "obs_"), (act, "act_"), (tc, "tc_"), (tr, "tr_"),
            (pol, "pol_"), (ar, "appr_"), (ad, "appd_"), (mem, "mem_"),
            (err, "err_"),
        ]:
            assert obj.id.startswith(prefix), (obj.__class__.__name__, obj.id)

    def test_two_instances_get_distinct_ids(self):
        a = Goal(description="x", success_criteria="y")
        b = Goal(description="x", success_criteria="y")
        assert a.id != b.id

    def test_timestamp_defaults_are_timezone_aware(self):
        t = Task(description="x")
        assert isinstance(t.created_at, datetime)
        assert t.created_at.tzinfo is not None


# ============================================================
# Status Literal guards
# ============================================================

class TestStatusLiterals:
    @pytest.mark.parametrize("status", ["pending", "in_progress", "done", "failed"])
    def test_goal_accepts_all_allowed_statuses(self, status):
        Goal(description="x", success_criteria="y", status=status)

    def test_goal_rejects_unknown_status(self):
        with pytest.raises(ValidationError):
            Goal(description="x", success_criteria="y", status="totally-made-up")

    def test_plan_step_rejects_unknown_status(self):
        with pytest.raises(ValidationError):
            PlanStep(
                plan_id="plan_x",
                order=1,
                action_spec={},
                expected_outcome="x",
                status="weird",
            )


# ============================================================
# Risk Literal on ApprovalRequest
# ============================================================

class TestRiskLiteral:
    @pytest.mark.parametrize(
        "risk", ["read_only", "reversible", "irreversible", "external"]
    )
    def test_approval_request_accepts_all_risks(self, risk):
        ApprovalRequest(action_id="a", step_id="s", risk=risk)

    def test_approval_request_rejects_unknown_risk(self):
        with pytest.raises(ValidationError):
            ApprovalRequest(action_id="a", step_id="s", risk="catastrophic")


# ============================================================
# PolicyDecision allows only allow/deny/escalate
# ============================================================

class TestPolicyDecisionLiteral:
    @pytest.mark.parametrize("d", ["allow", "deny", "escalate"])
    def test_accepts_allowed_decisions(self, d):
        PolicyDecision(policy_id="P", subject="s", action="a", decision=d)

    def test_rejects_unknown_decision(self):
        with pytest.raises(ValidationError):
            PolicyDecision(
                policy_id="P", subject="s", action="a", decision="maybe"
            )


# ============================================================
# ApprovalDecision allows only approve/deny/abort
# ============================================================

class TestApprovalDecisionLiteral:
    @pytest.mark.parametrize("d", ["approve", "deny", "abort"])
    def test_accepts_allowed_decisions(self, d):
        ApprovalDecision(request_id="r", decision=d)

    def test_rejects_unknown_decision(self):
        with pytest.raises(ValidationError):
            ApprovalDecision(request_id="r", decision="probably")


# ============================================================
# ToolResult: status Literal + optional output/error
# ============================================================

class TestToolResultShape:
    @pytest.mark.parametrize("s", ["success", "error", "timeout"])
    def test_status_literal(self, s):
        ToolResult(tool_call_id="x", status=s)

    def test_status_rejects_unknown(self):
        with pytest.raises(ValidationError):
            ToolResult(tool_call_id="x", status="cancelled")

    def test_output_and_error_default_none(self):
        r = ToolResult(tool_call_id="x", status="success")
        assert r.output is None
        assert r.error is None
        assert r.latency_ms == 0
        assert r.cost == 0.0


# ============================================================
# Action: type Literal + side_effects Literal
# ============================================================

class TestActionLiterals:
    @pytest.mark.parametrize("t", ["tool_call", "llm_synthesize", "output"])
    def test_type_literal(self, t):
        Action(step_id="s", type=t)

    @pytest.mark.parametrize("se", ["none", "read", "write", "external"])
    def test_side_effects_literal(self, se):
        Action(step_id="s", type="tool_call", side_effects=se)

    def test_action_rejects_unknown_type(self):
        with pytest.raises(ValidationError):
            Action(step_id="s", type="hack")

    def test_action_rejects_unknown_side_effect(self):
        with pytest.raises(ValidationError):
            Action(step_id="s", type="tool_call", side_effects="explode")


# ============================================================
# MemoryRecord: type Literal + defaults
# ============================================================

class TestMemoryRecord:
    @pytest.mark.parametrize(
        "t", ["working", "episodic", "semantic", "procedural"]
    )
    def test_type_literal(self, t):
        MemoryRecord(content="x", type=t)

    def test_rejects_unknown_type(self):
        with pytest.raises(ValidationError):
            MemoryRecord(content="x", type="psychic")

    def test_defaults(self):
        m = MemoryRecord(content="x")
        assert m.type == "working"
        assert m.owner == "session"
        assert m.tags == []
        assert m.ttl_seconds is None


# ============================================================
# ErrorObject: severity Literal + recoverable default
# ============================================================

class TestErrorObject:
    @pytest.mark.parametrize("sev", ["info", "warning", "error", "fatal"])
    def test_severity_literal(self, sev):
        ErrorObject(source="s", code="c", message="m", severity=sev)

    def test_rejects_unknown_severity(self):
        with pytest.raises(ValidationError):
            ErrorObject(source="s", code="c", message="m", severity="meh")

    def test_recoverable_defaults_false(self):
        e = ErrorObject(source="s", code="c", message="m")
        assert e.recoverable is False
        assert e.context == {}
