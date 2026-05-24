"""
tools/__init__.py — Public API of the Tool Layer

Usage:
    from tools import ToolRegistry, ToolExecutor, ToolResult
    from tools.builtins import CalculatorTool, DateTimeTool
"""

from .base import ToolBase, ToolResult, ToolSpec
from .executor import ToolExecutor
from .handler import ToolHandler
from .registry import ToolNotFoundError, ToolRegistry

__all__ = [
    "ToolBase",
    "ToolResult",
    "ToolSpec",
    "ToolRegistry",
    "ToolNotFoundError",
    "ToolExecutor",
    "ToolHandler",
]
