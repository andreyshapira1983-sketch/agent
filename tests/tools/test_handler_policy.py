"""Tests for the ToolHandler ↔ PolicyEngine second-line check."""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from brain.core import ThinkResult
from brain.policy import PolicyDecision, PolicyEngine, PolicyRule
from tools.base import ToolBase, ToolResult, ToolSpec
from tools.executor import ToolExecutor
from tools.handler import ToolHandler
from tools.registry import ToolRegistry


# ════════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════════

def _make_tool(name: str = "calc", *, destructive: bool = False, approval: bool = False) -> ToolBase:
    class _T(ToolBase):
        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(
                name=name,
                description="test",
                parameters={},
                is_destructive=destructive,
                requires_approval=approval,
            )
        def execute(self, **params) -> ToolResult:
            return self._ok(output="result_ok")
    return _T()


def _make_loop():
    loop = MagicMock()
    loop.submit = AsyncMock(return_value=True)
    return loop


def _think_for(tool_name: str, *, needs_approval: bool = False) -> ThinkResult:
    return ThinkResult(
        action="tool_call",
        content={"tool_name": tool_name, "params": {}},
        confidence=0.9,
        reasoning="test",
        needs_human_approval=needs_approval,
    )


# ════════════════════════════════════════════════════════════════════
# Tests
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_policy_deny_blocks_execution():
    """A DENY rule must stop the tool from running."""
    registry = ToolRegistry()
    tool = _make_tool("evil")
    registry.register(tool)
    executor = ToolExecutor(registry=registry)
    loop = _make_loop()

    deny_rule = PolicyRule(
        id="deny_evil",
        description="block evil",
        predicate=lambda req: req.target == "evil",
        verdict=PolicyDecision.DENY,
        reason="evil is not allowed",
    )
    engine = PolicyEngine([deny_rule])
    handler = ToolHandler(
        registry=registry,
        executor=executor,
        loop=loop,
        policy=engine,
        autonomy_level=5,  # high autonomy, but DENY still blocks
    )

    # Spy on tool execution
    executor.run = MagicMock(side_effect=lambda **kw: tool.execute(**kw.get("params", {})))

    await handler.handle(
        session_id="s",
        content={"tool_name": "evil", "params": {}},
        result=_think_for("evil"),
    )

    executor.run.assert_not_called()
    # Loop was fed a failure
    msg = loop.submit.call_args.args[0]
    assert msg.metadata["success"] is False
    assert "blocked by policy" in msg.metadata["error"]
    assert "deny_evil" in msg.metadata["error"]


@pytest.mark.asyncio
async def test_policy_require_approval_blocks_when_brain_missed_flag():
    """If policy says REQUIRE_APPROVAL but Brain didn't flag it, handler still blocks."""
    registry = ToolRegistry()
    tool = _make_tool("sensitive")
    registry.register(tool)
    executor = ToolExecutor(registry=registry)
    loop = _make_loop()

    rule = PolicyRule(
        id="approve_sensitive",
        description="approval gate",
        predicate=lambda req: req.target == "sensitive",
        verdict=PolicyDecision.REQUIRE_APPROVAL,
        reason="needs human eyes",
    )
    engine = PolicyEngine([rule])
    handler = ToolHandler(
        registry=registry,
        executor=executor,
        loop=loop,
        policy=engine,
        autonomy_level=4,
    )

    executor.run = MagicMock(side_effect=lambda **kw: tool.execute(**kw.get("params", {})))

    await handler.handle(
        session_id="s",
        content={"tool_name": "sensitive", "params": {}},
        result=_think_for("sensitive", needs_approval=False),  # Brain missed it
    )

    executor.run.assert_not_called()
    msg = loop.submit.call_args.args[0]
    assert "approval required" in msg.metadata["error"]


@pytest.mark.asyncio
async def test_policy_allow_lets_execution_proceed():
    registry = ToolRegistry()
    tool = _make_tool("safe_calc")
    registry.register(tool)
    executor = ToolExecutor(registry=registry)
    loop = _make_loop()

    engine = PolicyEngine([])  # no rules → fall back to default ALLOW
    handler = ToolHandler(
        registry=registry,
        executor=executor,
        loop=loop,
        policy=engine,
        autonomy_level=5,
    )

    await handler.handle(
        session_id="s",
        content={"tool_name": "safe_calc", "params": {}},
        result=_think_for("safe_calc"),
    )
    msg = loop.submit.call_args.args[0]
    assert msg.metadata["success"] is True
    assert msg.metadata["tool_name"] == "safe_calc"


@pytest.mark.asyncio
async def test_handler_works_without_policy_engine():
    """Backwards compatibility: existing wiring without a policy must keep working."""
    registry = ToolRegistry()
    tool = _make_tool("legacy")
    registry.register(tool)
    executor = ToolExecutor(registry=registry)
    loop = _make_loop()
    handler = ToolHandler(registry=registry, executor=executor, loop=loop)

    await handler.handle(
        session_id="s",
        content={"tool_name": "legacy", "params": {}},
        result=_think_for("legacy"),
    )
    msg = loop.submit.call_args.args[0]
    assert msg.metadata["success"] is True
