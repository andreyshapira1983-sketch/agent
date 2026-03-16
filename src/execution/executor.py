"""
Executor: run plan steps, call tools, return results.
"""
from __future__ import annotations

from typing import Any

from src.execution.context import ExecutionContext
from src.tools.orchestrator import run_tool


def execute_tool_step(tool_name: str, arguments: dict[str, Any] | None = None) -> str:
    return run_tool(tool_name, arguments)


def execute_steps(steps: list[dict[str, Any]], ctx: ExecutionContext | None = None) -> list[str]:
    """Execute a list of steps; each step: {tool, arguments}. Returns list of results."""
    ctx = ctx or ExecutionContext()
    out: list[str] = []
    for s in steps:
        if not ctx.can_continue():
            break
        name = s.get("tool") or s.get("name")
        args = s.get("arguments") or s.get("args") or {}
        if name:
            out.append(execute_tool_step(name, args))
            ctx.advance()
    return out
