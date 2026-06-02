"""Tool abstraction and registry.

A Tool is a callable unit with a risk label and a typed `run` method.
The registry resolves names to instances for the loop.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Literal

from core.ids import new_id
from core.models import ToolCall, ToolResult


Risk = Literal["read_only", "reversible", "irreversible", "external"]


def require_ascii_identifier(value: str, *, role: str) -> str:
    """Reject non-ASCII text where this codebase expects an identifier.

    Programming identifiers in this agent — file paths, shell argv
    elements, memory tags — MUST be ASCII. Russian (or any other
    non-ASCII text) belongs in *content*: the bytes inside a file, the
    body of a memory note, a web search query. Mixing the two creates
    encoding surprises on Windows (cp1251 → UTF-8 mojibake) and breaks
    cross-platform pipelines (cmd.exe argv handling, subprocess env,
    older filesystems).

    `role` is a short human-readable label used in the exception message
    (e.g. "file_write path", "shell_exec argv[1]"). Raises
    `PermissionError` so the tool / sanitiser surfaces a uniform
    rejection that the policy gate already knows how to log.
    """
    if not isinstance(value, str):
        raise PermissionError(
            f"{role} must be a string, got {type(value).__name__}"
        )
    if not value.isascii():
        # Build a small hint: show the first offending character so the
        # user / LLM can see what to fix, but never echo the entire
        # value (it might be long or contain unrelated data).
        bad = next((ch for ch in value if ord(ch) > 127), "")
        raise PermissionError(
            f"{role} must be ASCII-only; non-ASCII character "
            f"{bad!r} (U+{ord(bad):04X}) is not allowed. "
            "Use English identifiers (e.g. 'hello.txt' instead of 'привет.txt'); "
            "Russian or other languages remain fine inside file content, "
            "memory notes, and search queries."
        )
    return value


class Tool(ABC):
    name: str
    description: str
    risk: Risk = "read_only"

    @abstractmethod
    def run(self, **kwargs: Any) -> Any:
        """Execute the tool. Must raise on failure."""
        raise NotImplementedError

    def risk_for(self, arguments: dict[str, Any]) -> Risk:
        """Argument-dependent risk classification (§5 Action Risk & Reversibility).

        Most tools have a fixed risk class — `file_read` is always
        `read_only`, `web_search` is always `read_only`. But some tools
        (`file_write` is the canonical case) cross a trust boundary
        depending on what they're asked to do: writing a new file is
        reversible, *overwriting* an existing file is not.

        Default: return the static `self.risk`. Override to inspect
        `arguments` and pick a stricter (or laxer) class. The PolicyGate
        calls this method instead of reading `risk` directly.
        """
        return self.risk

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        """Tool Result Validation (§5).

        Returns (is_ok, issues):
          - is_ok=True  -> downstream may consume the output (warnings may still be present)
          - is_ok=False -> hard failure; loop must treat as verification error
          - issues      -> human-readable notes (empty list if perfectly clean)

        Default policy: any truthy output is OK, falsy -> hard fail.
        Tools should override for semantic checks (schema, freshness, emptiness, etc.).
        """
        if output is None:
            return False, ["output is None"]
        if isinstance(output, (str, list, dict)) and len(output) == 0:
            return False, ["empty output"]
        return True, []

    def invoke(self, call: ToolCall) -> ToolResult:
        """Wrap `run` with timing and error capture, returning a ToolResult."""
        started = time.perf_counter()
        try:
            output = self.run(**call.arguments)
            latency_ms = int((time.perf_counter() - started) * 1000)
            return ToolResult(
                tool_call_id=call.id,
                status="success",
                output=output,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return ToolResult(
                tool_call_id=call.id,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=latency_ms,
            )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found in registry")
        return self._tools[name]

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def describe(self) -> str:
        return "\n".join(f"- {t.name} ({t.risk}): {t.description}" for t in self._tools.values())
