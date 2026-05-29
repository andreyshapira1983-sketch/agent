"""File Read tool — sandboxed to the workspace root.

Refuses path traversal and oversized files. Returns plain text.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.base import Tool


MAX_BYTES = 1_000_000  # 1 MB hard cap for MVP


class FileReadTool(Tool):
    name = "file_read"
    description = "Read a UTF-8 text file from inside the workspace and return its contents."
    risk = "read_only"

    def __init__(self, workspace_root: Path | str):
        self.workspace_root = Path(workspace_root).resolve()

    def run(self, path: str) -> str:
        # Read-only file access may target user-supplied local documents
        # with non-ASCII names. We still keep the sandbox boundary strict:
        # path must be a non-empty string and must resolve inside workspace.
        if not isinstance(path, str):
            raise PermissionError(
                f"file_read path must be a string, got {type(path).__name__}"
            )
        if not path.strip():
            raise PermissionError("file_read path must be non-empty")
        target = (self.workspace_root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()

        try:
            target.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError(f"Path escapes workspace: {target}") from exc

        if not target.exists():
            raise FileNotFoundError(f"File not found: {target}")
        if not target.is_file():
            raise IsADirectoryError(f"Not a file: {target}")

        size = target.stat().st_size
        if size > MAX_BYTES:
            raise ValueError(f"File too large ({size} bytes > {MAX_BYTES})")

        # Strict UTF-8: a binary or wrong-encoding file must fail loudly so
        # the loop classifies it as a tool error, not as silently-garbled text.
        try:
            return target.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise UnicodeDecodeError(
                exc.encoding,
                exc.object,
                exc.start,
                exc.end,
                f"file is not valid UTF-8: {target.name} ({exc.reason})",
            ) from exc

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, str):
            return False, [f"expected str, got {type(output).__name__}"]
        if not output.strip():
            return False, ["file is empty or whitespace-only"]
        return True, []
