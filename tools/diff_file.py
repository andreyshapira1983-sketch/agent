"""MVP-13.1 — `diff_file` tool: unified diff vs proposed content.

The self-repair loop needs to show a user (and let the planner reason
about) exactly which lines a proposed code change would touch BEFORE
the change is applied. `diff_file` computes that diff against the
current on-disk content without writing anything itself.

This tool is the "show me what's about to change" surface for the
approval prompt — UX cribbed from `git diff` / `patch`.

Safety:
  - Paths must be ASCII and resolve INSIDE the workspace.
  - File content is read with a hard byte cap; oversize files are
    refused outright (the planner can fetch a narrower slice via
    file_read if needed).
  - `proposed_content` is size-capped the same way.
  - Read-only: nothing on disk changes.

Risk: read_only.
"""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from tools.base import Risk, Tool, require_ascii_identifier


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_BYTES = 1 * 1024 * 1024   # 1 MiB on each side
DEFAULT_CONTEXT_LINES = 3
MAX_CONTEXT_LINES = 20
MAX_DIFF_CHARS = 64 * 1024            # cap the unified-diff output


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class DiffFileTool(Tool):
    """Show a unified diff between a file and a proposed replacement."""

    name = "diff_file"
    description = (
        "Compute a unified diff between an existing file in the workspace "
        "and a proposed new content. Use this BEFORE applying a code "
        "change so the user can review what will change. Returns the "
        "diff, additions/deletions counts, and whether the file "
        "currently exists. Risk: read_only."
    )
    risk: Risk = "read_only"

    def __init__(
        self,
        workspace_root: Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ):
        if not workspace_root.is_dir():
            raise ValueError(
                f"workspace_root must be an existing directory, got {workspace_root}"
            )
        if max_bytes <= 0:
            raise ValueError(f"max_bytes must be > 0, got {max_bytes}")
        self.workspace_root = workspace_root.resolve()
        self.max_bytes = int(max_bytes)

    def risk_for(self, arguments: dict[str, Any]) -> Risk:  # noqa: ARG002
        return "read_only"

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------

    def run(
        self,
        path: str,
        proposed_content: str,
        context_lines: int = DEFAULT_CONTEXT_LINES,
    ) -> dict[str, Any]:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path must be a non-empty string")
        require_ascii_identifier(path, role="diff_file path")

        if not isinstance(proposed_content, str):
            raise ValueError("proposed_content must be a string")
        if len(proposed_content.encode("utf-8")) > self.max_bytes:
            raise ValueError(
                f"proposed_content too large (> {self.max_bytes} bytes)"
            )

        if not isinstance(context_lines, int):
            raise ValueError("context_lines must be an int")
        if context_lines < 0:
            raise ValueError(f"context_lines must be >= 0, got {context_lines}")
        if context_lines > MAX_CONTEXT_LINES:
            raise ValueError(
                f"context_lines must be <= {MAX_CONTEXT_LINES}, got {context_lines}"
            )

        # Resolve target & guard against traversal.
        target = (self.workspace_root / path).resolve() \
            if not Path(path).is_absolute() else Path(path).resolve()
        try:
            target.relative_to(self.workspace_root)
        except ValueError:
            raise PermissionError(
                f"path {path!r} resolves outside workspace"
            ) from None

        # Read current content if the file exists.
        file_exists = target.is_file()
        current_text = ""
        current_bytes = 0
        if file_exists:
            stat = target.stat()
            current_bytes = int(stat.st_size)
            if current_bytes > self.max_bytes:
                raise ValueError(
                    f"current file too large (> {self.max_bytes} bytes); refused"
                )
            current_text = target.read_text(encoding="utf-8")

        diff = self._unified_diff(
            current_text=current_text,
            proposed_text=proposed_content,
            display_path=str(target.relative_to(self.workspace_root))
            if file_exists else path,
            context_lines=context_lines,
        )
        adds, dels = self._count_changes(diff)
        # Cap output so the audit log stays bounded.
        diff_truncated = False
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + "\n... <diff truncated>\n"
            diff_truncated = True

        # Defence-in-depth: secrets in the file/proposed get redacted
        # before the diff leaves the tool boundary.
        from core.redaction import redact_text

        diff_safe, _ = redact_text(diff)

        return {
            "path": path,
            "file_exists": file_exists,
            "current_bytes": current_bytes,
            "proposed_bytes": len(proposed_content.encode("utf-8")),
            "additions": adds,
            "deletions": dels,
            "diff": diff_safe,
            "diff_truncated": diff_truncated,
            "compensation_plan": _NOOP_PLAN,
        }

    # ------------------------------------------------------------------
    # validate_output
    # ------------------------------------------------------------------

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, dict):
            return False, ["diff_file output must be a dict"]
        required = {
            "path", "file_exists", "current_bytes", "proposed_bytes",
            "additions", "deletions", "diff", "diff_truncated",
            "compensation_plan",
        }
        missing = required - output.keys()
        if missing:
            return False, [f"missing keys: {sorted(missing)}"]
        if not isinstance(output["file_exists"], bool):
            return False, ["file_exists must be a bool"]
        if not isinstance(output["diff_truncated"], bool):
            return False, ["diff_truncated must be a bool"]
        for k in ("current_bytes", "proposed_bytes", "additions", "deletions"):
            if not isinstance(output[k], int) or output[k] < 0:
                return False, [f"{k} must be a non-negative int"]
        if not isinstance(output["diff"], str):
            return False, ["diff must be a string"]
        return True, []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _unified_diff(
        *,
        current_text: str,
        proposed_text: str,
        display_path: str,
        context_lines: int,
    ) -> str:
        current_lines = current_text.splitlines(keepends=True)
        proposed_lines = proposed_text.splitlines(keepends=True)
        diff_iter = difflib.unified_diff(
            current_lines, proposed_lines,
            fromfile=f"a/{display_path}",
            tofile=f"b/{display_path}",
            n=context_lines,
        )
        return "".join(diff_iter)

    @staticmethod
    def _count_changes(diff_text: str) -> tuple[int, int]:
        """Count `+` / `-` lines that aren't the file headers."""
        adds = 0
        dels = 0
        for line in diff_text.splitlines():
            if not line:
                continue
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                adds += 1
            elif line.startswith("-"):
                dels += 1
        return adds, dels


_NOOP_PLAN = {
    "id": "noop",
    "actions": [{"kind": "noop", "description": "diff_file computes only"}],
    "tool_name": "diff_file",
    "description": "diff_file makes no changes; no rollback needed",
}
