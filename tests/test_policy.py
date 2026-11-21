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
from core.models import Action
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import Tool, ToolRegistry


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


# ---------- escalate_reversible_tools (agent/runtime write brake) ----------

def test_reversible_write_escalates_when_tool_is_in_escalate_set() -> None:
    """A reversible (new-file) write is promoted to escalate when the tool is
    listed — the human checkpoint before the agent/runtime creates a file."""
    reg = _registry(StubTool("file_write", "reversible"))
    gate = PolicyGate(reg)
    gate.escalate_reversible_tools = frozenset({"file_write"})

    decision = gate.check(_tool_call("file_write"))

    assert decision.decision == "escalate"
    assert any("requires approval" in r for r in decision.reasons)


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

    The FakePlanner sidesteps core.step_sanitizer.sanitize_step so we can deliver a
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
