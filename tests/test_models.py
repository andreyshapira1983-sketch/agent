"""Core Data Models (§12.1) — Pydantic Literal guards + defaults.

Every model in `core/models.py` is the bus between agent domains. A typo
in a `status` value or a missing default would let bad data flow between
the planner, executor, and memory. These tests pin the guard rails.
"""
from __future__ import annotations

from datetime import datetime

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


    def test_timestamp_defaults_are_timezone_aware(self):
        t = Task(description="x")
        assert isinstance(t.created_at, datetime)
        assert t.created_at.tzinfo is not None
