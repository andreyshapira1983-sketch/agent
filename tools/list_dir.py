"""List Directory tool — sandboxed to the workspace root.

Returns a text listing of files and subdirectories inside a workspace
directory. Refuses path traversal and non-directory paths.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.base import Tool


MAX_ENTRIES = 500  # safety cap to avoid enormous output


class ListDirTool(Tool):
    name = "list_dir"
    description = (
        "List the files and subdirectories inside a workspace directory. "
        "Pass '.' or '' to list the workspace root. "
        "Returns one entry per line; directories end with '/'."
    )
    risk = "read_only"

    def __init__(self, workspace_root: Path | str):
        self.workspace_root = Path(workspace_root).resolve()

    @staticmethod
    def _local_path(raw_path: str) -> Path:
        return Path(raw_path.strip().replace("\\", "/"))

    def run(self, path: str = ".") -> str:
        if not isinstance(path, str):
            raise PermissionError(
                f"list_dir path must be a string, got {type(path).__name__}"
            )
        raw = path.strip() or "."
        local_path = self._local_path(raw)
        target = (
            local_path.resolve()
            if local_path.is_absolute()
            else (self.workspace_root / local_path).resolve()
        )

        try:
            target.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError(f"Path escapes workspace: {target}") from exc

        if not target.exists():
            raise FileNotFoundError(f"Directory not found: {target}")
        if not target.is_dir():
            raise NotADirectoryError(f"Not a directory: {target}")

        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        if len(entries) > MAX_ENTRIES:
            entries = entries[:MAX_ENTRIES]
            truncated = True
        else:
            truncated = False

        lines: list[str] = []
        for entry in entries:
            if entry.is_dir():
                lines.append(entry.name + "/")
            else:
                lines.append(entry.name)

        result = "\n".join(lines)
        if truncated:
            result += f"\n... (truncated at {MAX_ENTRIES} entries)"
        return result if result else "(empty directory)"

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, str):
            return False, [f"expected str, got {type(output).__name__}"]
        return True, []
