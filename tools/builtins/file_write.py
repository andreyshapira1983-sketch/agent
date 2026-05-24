"""
tools/builtins/file_write.py — Generic file writer (sandboxed).

For when the agent needs to write something that doesn't have a dedicated
tool: JSON, plain text, .py source, .md, .csv. Anything binary should use
a format-specific writer (DocxWriter, XlsxWriter, PptxWriter).

Sandboxing
──────────
By default the tool only writes inside a configured workspace root.
Path traversal attempts (`..`, absolute paths outside the root) are
rejected. Pass `allow_outside_workspace=true` per-call to override —
this requires `policy/PolicyEngine` approval because the spec is
marked destructive AND it carries that flag in params.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ..base import ToolBase, ToolResult, ToolSpec

logger = logging.getLogger(__name__)


_MAX_BYTES = 10 * 1024 * 1024     # 10 MB hard cap
_DEFAULT_WORKSPACE = "data/workspace"
_TEXT_MODE_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".json", ".csv", ".tsv",
    ".html", ".htm", ".xml", ".yaml", ".yml",
    ".py", ".js", ".ts", ".sh", ".bat", ".ps1",
    ".log", ".ini", ".cfg", ".toml",
}


class FileWriteTool(ToolBase):
    """Write a file inside the agent's workspace."""

    def __init__(self, workspace_root: Path | str | None = None) -> None:
        self._workspace = Path(workspace_root or _DEFAULT_WORKSPACE).resolve()
        self._workspace.mkdir(parents=True, exist_ok=True)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="file_write",
            description=(
                "Записывает текстовый файл в sandbox-каталог агента. "
                "По умолчанию ограничен workspace_root."
            ),
            parameters={
                "path":      "str — относительный путь внутри workspace",
                "content":   "str — содержимое",
                "overwrite": "bool (optional, default false)",
                "append":    "bool (optional, default false)",
                "encoding":  "str (optional, default 'utf-8')",
                "allow_outside_workspace": "bool (optional, default false) — "
                                          "разрешить запись вне sandbox",
            },
            requires_approval=False,
            is_destructive=True,
        )

    # ────────────────────────────────────────────────────────────────

    def execute(self, **params: Any) -> ToolResult:
        raw_path = str(params.get("path", "")).strip()
        content = params.get("content", "")
        overwrite = bool(params.get("overwrite", False))
        append = bool(params.get("append", False))
        encoding = str(params.get("encoding", "utf-8"))
        allow_outside = bool(params.get("allow_outside_workspace", False))

        if not raw_path:
            return self._fail("'path' is required")
        if not isinstance(content, str):
            return self._fail("'content' must be a string")
        if overwrite and append:
            return self._fail("'overwrite' and 'append' are mutually exclusive")

        # Resolve destination relative to workspace unless allowed otherwise.
        try:
            target = self._resolve(raw_path, allow_outside=allow_outside)
        except ValueError as exc:
            return self._fail(str(exc))

        # Size check before write
        size = len(content.encode(encoding, errors="replace"))
        if size > _MAX_BYTES:
            return self._fail(f"content too large: {size} bytes (max {_MAX_BYTES})")

        # Existence policy
        existed = target.exists()
        if existed and not (overwrite or append):
            return self._fail(
                f"file exists: {target} (pass overwrite=true or append=true)"
            )

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with target.open(mode, encoding=encoding, errors="replace") as f:
                f.write(content)
        except OSError as exc:
            return self._fail(f"write failed: {exc}")

        return self._ok({
            "path":         str(target),
            "bytes":        target.stat().st_size,
            "existed":      existed,
            "mode":         "append" if append else "overwrite" if overwrite else "create",
            "encoding":     encoding,
            "outside_workspace": allow_outside,
        })

    # ────────────────────────────────────────────────────────────────

    def _resolve(self, raw_path: str, *, allow_outside: bool) -> Path:
        candidate = Path(raw_path).expanduser()
        if candidate.is_absolute():
            if not allow_outside:
                raise ValueError(
                    f"absolute path '{raw_path}' rejected — "
                    "set allow_outside_workspace=true to override"
                )
            return candidate.resolve()

        target = (self._workspace / candidate).resolve()
        try:
            target.relative_to(self._workspace)
        except ValueError:
            if not allow_outside:
                raise ValueError(
                    f"path '{raw_path}' escapes workspace {self._workspace} — "
                    "set allow_outside_workspace=true to override"
                )
        return target
