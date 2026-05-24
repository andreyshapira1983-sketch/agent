"""
tools/base.py — Base contract for all tools.

Every tool the Brain can call must implement ToolBase.
The Brain issues a `tool_call` action with a tool name + params.
The ToolExecutor finds the right tool and runs it.

Design principles:
    - Tools are PASSIVE — they wait to be called
    - Tools never call the Brain back directly
    - Tools return ToolResult (success or failure, always structured)
    - Tool execution is sandboxed: each call is independent
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """
    Structured result of a single tool execution.

    Brain always receives a ToolResult — never a raw exception.
    Errors are data, not crashes.
    """
    tool_name: str
    success: bool
    output: Any                          # Payload on success, None on failure
    error: str | None = None             # Human-readable error on failure
    metadata: dict[str, Any] = field(default_factory=dict)  # duration_ms, retries, etc.

    # ------------------------------------------------------------------
    def as_text(self) -> str:
        """Compact string the Brain can store in memory or pass to LLM."""
        if self.success:
            return f"[{self.tool_name}] OK: {self.output}"
        return f"[{self.tool_name}] ERROR: {self.error}"

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "success":   self.success,
            "output":    self.output,
            "error":     self.error,
            "metadata":  self.metadata,
        }


@dataclass
class ToolSpec:
    """
    Describes what a tool does — used by ToolRegistry for discovery.
    Brain can query the registry to choose the right tool.
    """
    name: str
    description: str
    parameters: dict[str, str]   # param_name → human-readable type hint
    requires_approval: bool = False  # If True → BrainLoop asks human before running
    is_destructive: bool = False     # Writes/deletes data — extra caution


class ToolBase(ABC):
    """
    Abstract base class for all tools.

    Subclass this and implement `spec` + `execute`.
    Register with ToolRegistry.
    """

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """Return static description of this tool."""

    @abstractmethod
    def execute(self, **params: Any) -> ToolResult:
        """
        Run the tool synchronously.
        Must NEVER raise — catch all exceptions and return ToolResult(success=False).
        """

    # ------------------------------------------------------------------
    # Helpers for subclasses
    # ------------------------------------------------------------------

    def _ok(self, output: Any, **meta: Any) -> ToolResult:
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            output=output,
            metadata=meta,
        )

    def _fail(self, error: str, **meta: Any) -> ToolResult:
        return ToolResult(
            tool_name=self.spec.name,
            success=False,
            output=None,
            error=error,
            metadata=meta,
        )

    def _timed(self, fn, *args, **kwargs) -> tuple[Any, float]:
        """Call fn(*args, **kwargs) and return (result, elapsed_ms)."""
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        elapsed = (time.perf_counter() - t0) * 1000
        return result, elapsed
