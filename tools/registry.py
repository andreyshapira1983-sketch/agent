"""
tools/registry.py — Tool Registry

Central catalog of all registered tools.
Brain queries the registry to:
    - Discover what tools exist
    - Look up a tool by name before execution
    - Check whether a tool requires human approval

Design:
    - Single registry instance per agent runtime
    - Tools registered at startup (not dynamically)
    - Thread-safe reads (no mutation after startup)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import ToolBase, ToolSpec

logger = logging.getLogger(__name__)


class ToolNotFoundError(KeyError):
    """Raised when Brain requests a tool that isn't registered."""


class ToolRegistry:
    """
    Maps tool names → ToolBase instances.

    Usage:
        registry = ToolRegistry()
        registry.register(WebSearchTool())
        registry.register(ReadFileTool())

        tool = registry.get("web_search")
        result = tool.execute(query="Python asyncio")
    """

    def __init__(self) -> None:
        self._tools: dict[str, "ToolBase"] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, tool: "ToolBase") -> "ToolRegistry":
        """Register a tool. Raises if name already taken."""
        name = tool.spec.name
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered")
        self._tools[name] = tool
        logger.info("[Registry] Registered tool: %s", name)
        return self  # chainable

    def register_many(self, *tools: "ToolBase") -> "ToolRegistry":
        for t in tools:
            self.register(t)
        return self

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> "ToolBase":
        """Return tool by name. Raises ToolNotFoundError if missing."""
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(
                f"Tool '{name}' not found. Available: {self.names()}"
            )

    def has(self, name: str) -> bool:
        return name in self._tools

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def specs(self) -> list["ToolSpec"]:
        """Return all tool specs — Brain can include these in LLM context."""
        return [t.spec for t in self._tools.values()]

    def specs_as_text(self) -> str:
        """
        Compact text block for LLM context injection.
        Brain calls this to tell the LLM what tools are available.
        """
        lines = ["Available tools:"]
        for spec in sorted(self.specs(), key=lambda s: s.name):
            approval = " [requires_approval]" if spec.requires_approval else ""
            destructive = " [destructive]" if spec.is_destructive else ""
            params = ", ".join(f"{k}: {v}" for k, v in spec.parameters.items())
            lines.append(f"  {spec.name}{approval}{destructive} — {spec.description}")
            if params:
                lines.append(f"    params: ({params})")
        return "\n".join(lines)

    def requires_approval(self, name: str) -> bool:
        """Check if a tool needs human sign-off before execution."""
        return self.get(name).spec.requires_approval

    def is_destructive(self, name: str) -> bool:
        return self.get(name).spec.is_destructive

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        return f"ToolRegistry({self.names()})"
