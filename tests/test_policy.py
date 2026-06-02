"""Tests for the Policy Gate (§5 Action Risk & Reversibility, §12.4).

Unit branch: every gate branch is exercised with a stub tool of the appropriate
risk label. The gate must:
  - allow read_only and reversible actions,
  - escalate irreversible and external actions,
  - deny unknown tools and malformed tool_calls,
  - allow internal (non-tool) actions like llm_synthesize/output.

Integration branch: when the gate refuses, the Executor MUST NOT touch the
tool. No tool_call / tool_result / verify events for the blocked step. This
is the most important safety invariant of the whole runtime.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.models import Action, PolicyDecision
from core.policy import PolicyGate, POLICY_ID
from tools.base import Tool, ToolRegistry
from tests.conftest import FakeLLM, FakePlanner


class StubTool(Tool):
    """A no-op tool with configurable name + risk.

    `run()` raises if called — that is the contract test for the integration
    cases: when the policy blocks the action, this tool's `run` must never
    be reached.
    """

    def __init__(self, name: str, risk: Literal["read_only", "reversible", "irreversible", "external"]):
        self.name = name
        self.description = f"stub:{name}"
        self.risk = risk

    def run(self, **kwargs: Any) -> Any:
        raise AssertionError(
            f"StubTool({self.name}, risk={self.risk}).run() was called — "
            "policy should have blocked this action before execution."
        )


def _registry(*tools: Tool) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def _tool_call(tool_name: str | None) -> Action:
    return Action(
        step_id="step_test",
        type="tool_call",
        tool_name=tool_name,
        parameters={},
        side_effects="read",
    )


# ---------- allow paths ----------

def test_read_only_tool_is_allowed() -> None:
    reg = _registry(StubTool("r", "read_only"))
    gate = PolicyGate(reg)

    decision = gate.check(_tool_call("r"))

    assert isinstance(decision, PolicyDecision)
    assert decision.policy_id == POLICY_ID
    assert decision.decision == "allow"
    assert decision.subject == "r"
    assert any("read-only" in r for r in decision.reasons)


def test_reversible_tool_is_allowed_with_audit_reason() -> None:
    reg = _registry(StubTool("rv", "reversible"))
    gate = PolicyGate(reg)

    decision = gate.check(_tool_call("rv"))

    assert decision.decision == "allow"
    assert any("reversible" in r for r in decision.reasons)


# ---------- escalate paths ----------

def test_irreversible_tool_is_escalated() -> None:
    reg = _registry(StubTool("ir", "irreversible"))
    gate = PolicyGate(reg)

    decision = gate.check(_tool_call("ir"))

    assert decision.decision == "escalate"
    assert any("irreversible" in r for r in decision.reasons)


def test_external_side_effect_tool_is_escalated() -> None:
    reg = _registry(StubTool("ext", "external"))
    gate = PolicyGate(reg)

    decision = gate.check(_tool_call("ext"))

    assert decision.decision == "escalate"
    assert any("external" in r for r in decision.reasons)


# ---------- deny paths ----------

def test_unknown_tool_is_denied() -> None:
    reg = _registry(StubTool("only_known", "read_only"))
    gate = PolicyGate(reg)

    decision = gate.check(_tool_call("does_not_exist"))

    assert decision.decision == "deny"
    assert decision.subject == "does_not_exist"
    assert any("not in registry" in r for r in decision.reasons)


def test_tool_call_without_tool_name_is_denied() -> None:
    reg = _registry(StubTool("r", "read_only"))
    gate = PolicyGate(reg)

    decision = gate.check(_tool_call(tool_name=None))

    assert decision.decision == "deny"
    assert any("without tool_name" in r for r in decision.reasons)


# ---------- non-tool actions ----------

def test_llm_synthesize_action_is_allowed() -> None:
    reg = _registry()
    gate = PolicyGate(reg)

    action = Action(
        step_id="step_test",
        type="llm_synthesize",
        tool_name=None,
        parameters={},
        side_effects="none",
    )
    decision = gate.check(action)

    assert decision.decision == "allow"
    assert any("non-tool" in r for r in decision.reasons)


def test_output_action_is_allowed() -> None:
    reg = _registry()
    gate = PolicyGate(reg)

    action = Action(
        step_id="step_test",
        type="output",
        tool_name=None,
        parameters={},
        side_effects="none",
    )
    decision = gate.check(action)
    assert decision.decision == "allow"


# ---------- decision invariants ----------

def test_decision_subject_matches_tool_name_when_allowed() -> None:
    reg = _registry(StubTool("named", "read_only"))
    gate = PolicyGate(reg)

    decision = gate.check(_tool_call("named"))

    assert decision.subject == "named"
    assert decision.action == "tool_call"
    # Decisions always carry the policy id (audit trail invariant)
    assert decision.policy_id == POLICY_ID


# ==========================================================================
# Integration: policy decision propagates through the Control Loop.
# ==========================================================================

def _events(log_path: Path) -> list[dict]:
    events = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _build_loop_with_planned_step(
    workspace: Path,
    tool: Tool,
    planned_step: dict[str, Any],
) -> tuple[AgentLoop, Path]:
    """Wire up an AgentLoop whose planner deterministically emits one step.

    The FakePlanner sidesteps LLMPlanner._sanitize_step so we can deliver a
    plan that the real planner would never produce — exactly what we need to
    exercise the Executor's own safety net.

    `max_replan_attempts=1` disables re-planning (post-MVP-8 default is 3),
    so the policy-gate / approval-gate tests below remain single-attempt
    and their event counts stay deterministic.
    """
    registry = ToolRegistry()
    registry.register(tool)
    policy = PolicyGate(registry)
    planner = FakePlanner(sources=[planned_step])
    llm = FakeLLM(responses=["irrelevant: synthesis must not run when no artifacts"])
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=registry,
        policy=policy,
        llm=llm,
        logger=logger,
        planner=planner,
        max_replan_attempts=1,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


def test_escalated_tool_never_runs_in_loop(workspace: Path) -> None:
    """Irreversible tool with NO approval provider wired: policy returns
    `escalate` -> Executor must not call .run(). This is the safe default
    pre- and post-MVP-6.
    """
    tool = StubTool("dangerous_write", "irreversible")
    planned_step = {
        "tool": "dangerous_write",
        "arguments": {"x": 1},
        "label": "stub:dangerous_write",
        "expected_outcome": "should never execute",
    }
    agent, log_path = _build_loop_with_planned_step(workspace, tool, planned_step)

    # StubTool.run() asserts if reached. If we make it past `agent.run()`
    # without an exception, the safety invariant held — the tool's run()
    # was never called. Helper builds with max_replan_attempts=1 so the
    # event counts below are deterministic (single attempt).
    agent.run(user_question="please use the dangerous tool", file_hint=None)

    events = _events(log_path)
    event_names = [e["event"] for e in events]

    # The policy event MUST appear with decision=escalate
    policy_events = [e for e in events if e["event"] == "policy"]
    assert len(policy_events) == 1
    assert policy_events[0]["payload"]["decision"] == "escalate"
    assert policy_events[0]["payload"]["subject"] == "dangerous_write"

    # The executor's defining safety invariant: NO tool execution events.
    assert "tool_call" not in event_names
    assert "tool_result" not in event_names
    assert "verify" not in event_names

    # Without an approval provider the escalate must surface as
    # `approval_unavailable` — refusing rather than running.
    error_events = [e for e in events if e["event"] == "error"]
    assert any(
        ev["payload"]["code"] == "approval_unavailable" for ev in error_events
    ), [ev["payload"]["code"] for ev in error_events]


def test_denied_unknown_tool_never_runs_in_loop(workspace: Path) -> None:
    """Tool not in registry: policy returns `deny` -> Executor must not call anything."""
    # Register a benign tool so the registry is not empty; planner picks something else.
    benign = StubTool("benign", "read_only")
    planned_step = {
        "tool": "ghost_tool",  # NOT registered
        "arguments": {},
        "label": "stub:ghost_tool",
        "expected_outcome": "should never execute",
    }
    agent, log_path = _build_loop_with_planned_step(workspace, benign, planned_step)

    # max_replan_attempts=1 keeps a single attempt; we then assert the
    # policy gate fired once with `deny` and no tool execution events fired.
    agent.run(user_question="invoke a ghost", file_hint=None)

    events = _events(log_path)
    event_names = [e["event"] for e in events]

    policy_events = [e for e in events if e["event"] == "policy"]
    assert len(policy_events) == 1
    assert policy_events[0]["payload"]["decision"] == "deny"
    assert policy_events[0]["payload"]["subject"] == "ghost_tool"

    assert "tool_call" not in event_names
    assert "tool_result" not in event_names
    assert "verify" not in event_names


# ==========================================================================
# MVP-9: argument-aware risk (Tool.risk_for) drives the gate decision.
# ==========================================================================

class DynamicRiskTool(Tool):
    """Static risk is the strict fallback; `risk_for` may downgrade it
    based on arguments. Models the `file_write` pattern."""

    name = "dyn"
    description = "dynamic-risk stub"
    risk = "irreversible"  # static, conservative

    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def risk_for(self, arguments: dict[str, Any]):
        # Downgrade to reversible when the path looks "new".
        if arguments.get("path") == "new":
            return "reversible"
        return "irreversible"

    def run(self, **kwargs):
        self.calls.append(dict(kwargs))
        return "ok"


def test_risk_for_downgrades_to_reversible_when_args_say_so() -> None:
    reg = _registry(DynamicRiskTool())
    gate = PolicyGate(reg)

    action = Action(
        step_id="step_x",
        type="tool_call",
        tool_name="dyn",
        parameters={"path": "new"},
        side_effects="write",
    )
    decision = gate.check(action)

    # Even though `dyn.risk == "irreversible"`, the dynamic call
    # classified this argument set as `reversible` and the gate allows.
    assert decision.decision == "allow"
    assert any("reversible" in r for r in decision.reasons)


def test_risk_for_stays_irreversible_for_unsafe_args() -> None:
    reg = _registry(DynamicRiskTool())
    gate = PolicyGate(reg)

    action = Action(
        step_id="step_y",
        type="tool_call",
        tool_name="dyn",
        parameters={"path": "exists"},
        side_effects="write",
    )
    decision = gate.check(action)

    assert decision.decision == "escalate"
    assert any("irreversible" in r for r in decision.reasons)


def test_risk_for_receives_empty_dict_when_parameters_is_None() -> None:
    """Defensive: PolicyGate must call `risk_for({})` when the action's
    parameters dict is missing / empty, instead of crashing."""

    class _SeesArgs(Tool):
        name = "seesargs"
        description = "captures the dict it was passed"
        risk = "read_only"

        def __init__(self):
            self.last_args: dict | None = None

        def risk_for(self, arguments):
            self.last_args = arguments
            return self.risk

        def run(self, **kwargs):
            return "x"

    tool = _SeesArgs()
    reg = _registry(tool)
    gate = PolicyGate(reg)

    action = Action(step_id="s", type="tool_call", tool_name="seesargs")
    gate.check(action)

    assert tool.last_args == {}


def test_risk_for_is_consulted_per_call_not_cached() -> None:
    """Same tool, two different argument sets, two different decisions —
    proves the gate calls `risk_for` fresh each time."""
    reg = _registry(DynamicRiskTool())
    gate = PolicyGate(reg)

    safe = Action(step_id="s1", type="tool_call", tool_name="dyn", parameters={"path": "new"})
    danger = Action(step_id="s2", type="tool_call", tool_name="dyn", parameters={"path": "old"})

    assert gate.check(safe).decision == "allow"
    assert gate.check(danger).decision == "escalate"
