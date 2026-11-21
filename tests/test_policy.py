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

from typing import Any, Literal

from core.models import Action
from core.policy import PolicyGate
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
