"""Read-only workspace search: files by name, lines by text; credential files are never read."""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from tools.base import Tool
from tools.file_read import _is_credential_path

MAX_RESULTS = 200
DEFAULT_RESULTS = 50
MAX_FILE_BYTES = 50_000_000
_SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"})
_LINE_CHARS = 240
#: The agent's own journal and memory, searched only when `path` points inside them:
#: a workspace-wide search would otherwise find the echo of its own query in this turn's log.
_OWN_RUNTIME_DIRS = frozenset({"logs", "data"})


def _hit_text(line: str, matcher: re.Pattern[str]) -> str:
    """Cut a long line around its first match, not at its head."""
    if len(line) <= _LINE_CHARS:
        return line
    m = matcher.search(line)
    start = 0 if m is None else max(0, min(m.start() - _LINE_CHARS // 3, len(line) - _LINE_CHARS))
    end = start + _LINE_CHARS
    return ("…" if start else "") + line[start:end] + ("…" if end < len(line) else "")


class FindInFilesTool(Tool):
    name = "find_in_files"
    description = (
        "Search the workspace. With `query`: every line containing it, as "
        "'path:line: text' — the line number feeds file_read(start_line=...). "
        "Without `query`: the files whose NAME matches `name` (a glob such as "
        "'*Lebl*' or '*.txt'). Recursive under `path`; case-insensitive; "
        "`regex=true` treats query as a regular expression. A negative answer "
        "states how many files were searched. YOUR OWN journals and memory "
        "(logs/, data/) are skipped by a workspace-wide search — to search them "
        "point `path` at them, e.g. path='data' or path='data/approval_inbox.jsonl'."
    )
    risk = "read_only"

    def __init__(self, workspace_root: Path | str):
        self.workspace_root = Path(workspace_root).resolve()

    def _files(self, root: Path, name: str) -> list[Path]:
        out: list[Path] = []
        pattern = (name or "*").lower()
        inside_own = root != self.workspace_root and root.relative_to(self.workspace_root).parts[0] in _OWN_RUNTIME_DIRS
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(self.workspace_root)
            if any(part in _SKIP_DIRS for part in rel.parts) or not path.is_file():
                continue
            if not inside_own and len(rel.parts) > 1 and rel.parts[0] in _OWN_RUNTIME_DIRS:
                continue
            if _is_credential_path(rel.as_posix()):
                continue
            # A link or junction on the way: what gets read is its target.
            try:
                real = path.resolve().relative_to(self.workspace_root)
            except (ValueError, OSError):
                continue
            if _is_credential_path(real.as_posix()):
                continue
            if fnmatch.fnmatch(path.name.lower(), pattern):
                out.append(path)
        return out

    def run(
        self,
        query: str = "",
        path: str = ".",
        name: str = "*",
        regex: bool = False,
        max_results: int = DEFAULT_RESULTS,
    ) -> str:
        if not isinstance(path, str) or not isinstance(name, str) or not isinstance(query, str):
            raise TypeError("find_in_files: query, path and name must be strings")
        root = (self.workspace_root / (path.strip().replace("\\", "/") or ".")).resolve()
        try:
            root.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError(f"Path escapes workspace: {root}") from exc
        # A file path searches inside that file.
        if root.is_file():
            if _is_credential_path(root.relative_to(self.workspace_root).as_posix()):
                raise PermissionError(f"refusing to search a credential file: {path!r}")
            files = [root]
        elif root.is_dir():
            files = self._files(root, name)
        else:
            from tools.base import truncated_name_hint
            raise FileNotFoundError(
                f"Not found: {path}"
                + truncated_name_hint(self.workspace_root, root)
            )
        limit = max(1, min(int(max_results or DEFAULT_RESULTS), MAX_RESULTS))
        where = f"under {path or '.'} (name={name or '*'})"
        if not query:
            shown = [f.relative_to(self.workspace_root).as_posix() for f in files[:limit]]
            head = f"{len(files)} files {where}" + (f", showing {limit}" if len(files) > limit else "")
            return "\n".join([head, *shown])

        matcher = re.compile(query if regex else re.escape(query), re.IGNORECASE)
        hits: list[str] = []
        total = occurrences = matched_files = searched = 0
        for f in files:
            if f.stat().st_size > MAX_FILE_BYTES:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # not text, so not searched by line
            searched += 1
            rel = f.relative_to(self.workspace_root).as_posix()
            found_here = False
            for n, line in enumerate(text.splitlines(), 1):
                found = len(matcher.findall(line))
                if found:
                    total += 1
                    occurrences += found
                    found_here = True
                    if len(hits) < limit:
                        hits.append(f"{rel}:{n}: {_hit_text(line.strip(), matcher)}")
            matched_files += found_here
        if not total:
            return f"no matches for {query!r} in {searched} text files {where}"
        # Lines and occurrences are different numbers; both are reported.
        head = (f"{total} matching lines ({occurrences} occurrences) in {matched_files} "
                f"of {searched} text files {where}")
        if total > limit:
            head += f", showing {limit}"
        return "\n".join([head, *hits])

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, str):
            return False, [f"expected str, got {type(output).__name__}"]
        return True, []
