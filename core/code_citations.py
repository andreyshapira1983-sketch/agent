"""Memory references to code are checked against the code at read time.

    A reference is stale when the named file or name (`path::name` or `path:name`)
    no longer exists; the record is still delivered, flagged as needing a check.
    Memory is never deleted or rewritten here."""
from __future__ import annotations

import ast
import re
from pathlib import Path

_CITE_RE = re.compile(
    r"(?<![\w/.])((?:core|tools|cli|app|api|tests|scripts)/[A-Za-z0-9_/]+\.py)"
    # A name is at least three characters: error signatures carry line numbers as `:N`, which is not a name.
    r"(?:::?([A-Za-z_][A-Za-z0-9_]{2,}))?"
)
_names_cache: dict[tuple[str, float], frozenset[str]] = {}


def _names(path: Path) -> frozenset[str] | None:
    """Top-level and nested names of a file: functions, classes, assignments."""
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return None
    if key not in _names_cache:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            return None
        found = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        found |= {t.id for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))
                  for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)}
        _names_cache[key] = frozenset(found)
    return _names_cache[key]


def stale_refs(root: Path | str, text: str) -> list[str]:
    """Code references in the text that no longer exist in the working folder."""
    out: list[str] = []
    for m in _CITE_RE.finditer(text or ""):
        rel, name = m.group(1), m.group(2)
        path = Path(root) / rel
        if not path.is_file():
            ref = f"{rel} (файла больше нет)"
        elif name and (names := _names(path)) is not None and name not in names:
            ref = f"{rel}::{name} (имени в файле больше нет)"
        else:
            continue
        if ref not in out:
            out.append(ref)
    return out


def annotate(root: Path | str | None, text: str) -> str:
    """The record text, plus a note if its code references are stale."""
    if root is None:
        return text
    stale = stale_refs(root, text)
    if not stale:
        return text
    return f"{text} [АДРЕС УСТАРЕЛ: {'; '.join(stale[:3])} — сверь с кодом, прежде чем опираться]"


def annotate_lines(root: Path | str | None, block: str) -> tuple[str, int]:
    """Mark each line of a block separately; return the block and the number of marks."""
    if root is None or not block:
        return block, 0
    lines, marked = [], 0
    for line in block.split("\n"):
        new = annotate(root, line)
        marked += new is not line
        lines.append(new)
    return "\n".join(lines), marked
