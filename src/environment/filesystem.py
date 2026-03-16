"""
Filesystem access within allowed paths. Security/Sandbox must approve.
"""
from __future__ import annotations

from pathlib import Path

_allowed_roots: set[Path] = set()
_SKIP_TREE_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
}


def set_allowed_roots(roots: list[str] | list[Path]) -> None:
    _allowed_roots.clear()
    _allowed_roots.update(Path(r).resolve() for r in roots)


def _is_allowed_path(path: Path) -> bool:
    return not _allowed_roots or any(str(path).startswith(str(root)) for root in _allowed_roots)


def _sorted_children(path: Path) -> list[Path]:
    return sorted(path.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.lower()))


def _tree_label(path: Path) -> str:
    name = path.name or "."
    return f"{name}/" if path.is_dir() else name


def build_tree_snapshot(path: str | Path, max_depth: int = 3, max_entries: int = 200) -> str:
    root = Path(path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    max_depth = max(0, int(max_depth))
    max_entries = max(1, int(max_entries))
    lines: list[str] = [_tree_label(root)]
    visited = 0
    truncated = False

    def walk(current: Path, depth: int, indent: str) -> None:
        nonlocal visited, truncated
        if truncated or depth >= max_depth or not current.is_dir():
            return
        try:
            children = [entry for entry in _sorted_children(current) if entry.name not in _SKIP_TREE_NAMES]
        except OSError:
            lines.append(f"{indent}[unavailable]")
            return
        for child in children:
            if visited >= max_entries:
                truncated = True
                return
            suffix = "/" if child.is_dir() else ""
            lines.append(f"{indent}{child.name}{suffix}")
            visited += 1
            if child.is_dir():
                walk(child, depth + 1, indent + "  ")

    walk(root, 0, "  ")
    if truncated:
        lines.append("  ... [truncated]")
    return "\n".join(lines)


def list_dir(path: str) -> list[str]:
    p = Path(path).resolve()
    if not _is_allowed_path(p):
        raise PermissionError(f"Path not allowed: {path}")
    return [x.name for x in p.iterdir()]


def read_file(path: str) -> str:
    p = Path(path).resolve()
    if not _is_allowed_path(p):
        raise PermissionError(f"Path not allowed: {path}")
    return p.read_text(encoding="utf-8", errors="replace")


def describe_tree(path: str, max_depth: int = 3, max_entries: int = 200) -> str:
    p = Path(path).resolve()
    if not _is_allowed_path(p):
        raise PermissionError(f"Path not allowed: {path}")
    return build_tree_snapshot(p, max_depth=max_depth, max_entries=max_entries)
