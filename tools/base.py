"""Tool abstraction (a callable unit with a risk label) and the name-to-tool registry."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Literal

from core.models import ToolCall, ToolResult

Risk = Literal["read_only", "reversible", "irreversible", "external"]


def require_ascii_identifier(value: str, *, role: str) -> str:
    """Reject non-ASCII text where an identifier (path, argv element, memory tag) is expected.

    Non-ASCII belongs in content; in identifiers it causes cp1251 mojibake and argv breakage.
    Raises PermissionError, which the policy gate already logs; `role` names the field.
    """
    if not isinstance(value, str):
        raise PermissionError(
            f"{role} must be a string, got {type(value).__name__}"
        )
    if not value.isascii():
        # Name the first offending character only; never echo the whole value.
        bad = next((ch for ch in value if ord(ch) > 127), "")
        raise PermissionError(
            f"{role} must be ASCII-only; non-ASCII character "
            f"{bad!r} (U+{ord(bad):04X}) is not allowed. "
            "Use English identifiers (e.g. 'hello.txt' instead of 'привет.txt'); "
            "Russian or other languages remain fine inside file content, "
            "memory notes, and search queries."
        )
    return value


# How many real names the missing-path hint shows.
_MAX_HINT_NAMES = 8


def truncated_name_hint(workspace_root, target) -> str:
    """Hint for a missing path: the folder's names that continue the given (truncated) name."""
    from pathlib import Path

    try:
        root = Path(workspace_root).resolve()
        target = Path(target)
        parent = target.parent if target.parent != target else root
        if not parent.is_absolute():
            parent = root / parent
        parent = parent.resolve()
        parent.relative_to(root)
    except (OSError, ValueError):
        return ""
    if not parent.is_dir():
        return ""

    stem = target.name
    try:
        names = sorted(p.name for p in parent.iterdir() if not p.name.startswith("."))
    except OSError:
        return ""
    if not names:
        return ""

    if stem:
        starts = [n for n in names if n.startswith(stem) and n != stem]
        if starts:
            shown = ", ".join(starts[:_MAX_HINT_NAMES])
            more = "" if len(starts) <= _MAX_HINT_NAMES else f" (+{len(starts) - _MAX_HINT_NAMES})"
            return (
                f". Похоже на УРЕЗАННОЕ имя: в этой папке есть {len(starts)} "
                f"имя(ён), начинающихся с '{stem}' — {shown}{more}. "
                "Возьми полное имя целиком; обрывок шестнадцатеричного "
                "хвоста от целого не отличается на вид."
            )

    # No continuation, no hint: listing the folder is file_read._nearest_dir_hint's job.
    return ""


def normalize_slug(text: str) -> str:
    """Lowercase ASCII slug: non-alphanumeric runs become one hyphen."""
    import re

    if not isinstance(text, str):
        raise TypeError(f"normalize_slug expects str, got {type(text).__name__}")
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower())
    slug = slug.strip("-")
    return slug or "untitled"


class Tool(ABC):
    name: str
    description: str
    risk: Risk = "read_only"
    # Argument contract in words (names, types, allowed values); empty means the planner guesses.
    arguments: str = ""

    @abstractmethod
    def run(self, **kwargs: Any) -> Any:
        """Execute the tool. Must raise on failure."""
        raise NotImplementedError

    def risk_for(self, arguments: dict[str, Any]) -> Risk:
        """Risk class for these arguments; the PolicyGate calls this, default is `self.risk`.

        Override when the arguments decide the class (file_write: overwriting is irreversible).
        """
        return self.risk

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        """Return (is_ok, issues); is_ok=False is a hard failure (a verification error).

        Default: None or an empty str/list/dict fails. Override for semantic checks.
        """
        if output is None:
            return False, ["output is None"]
        if isinstance(output, (str, list, dict)) and len(output) == 0:
            return False, ["empty output"]
        return True, []

    def execution_status(self, output: Any) -> str:
        """Whether the requested work succeeded; "success" whenever `run` returned.

        Override when a call can return while its work fails (shell). validate_output asks a
        different question (is the output well-formed); a correct failure report is valid output.
        """
        return "success"

    def invoke(self, call: ToolCall) -> ToolResult:
        """Wrap `run` with timing and error capture, returning a ToolResult."""
        started = time.perf_counter()
        try:
            output = self.run(**call.arguments)
            latency_ms = int((time.perf_counter() - started) * 1000)
            result = ToolResult(
                tool_call_id=call.id,
                status="success" if self.execution_status(output) == "success" else "error",
                output=output,
                latency_ms=latency_ms,
            )
        except Exception as exc:  # noqa: BLE001 — every tool failure becomes an error ToolResult
            latency_ms = int((time.perf_counter() - started) * 1000)
            result = ToolResult(
                tool_call_id=call.id,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=latency_ms,
            )
        try:
            from core.tool_receipts import record_tool_invoke_receipt

            record_tool_invoke_receipt(self, call, result)
        except Exception:  # noqa: BLE001, S110 — receipts must never break tool execution
            pass
        return result


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
        return "\n".join(
            f"- {t.name} ({t.risk}): {t.description}"
            + (f" Arguments: {t.arguments}" if getattr(t, "arguments", "") else "")
            for t in self._tools.values()
        )

    def argument_contracts(self, *, hidden: frozenset[str] = frozenset()) -> str:
        """Argument contracts of the tools that declare one, for the planner."""
        lines = [
            f"- {t.name}: {t.arguments}"
            for t in self._tools.values()
            if getattr(t, "arguments", "") and t.name not in hidden
        ]
        return "\n".join(lines)
