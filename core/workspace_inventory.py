"""Workspace inventory for goal choice: what is on disk and which tools are open."""
from __future__ import annotations

from pathlib import Path

# Service folders of the workspace (logs, state, caches), not content.
_INVENTORY_SKIP = frozenset({".git", "logs", "data", "__pycache__", ".venv", "venv", ".pytest_cache",
                             ".ruff_cache", "node_modules", "runtime", ".github", ".codacy"})
_INVENTORY_LINES = 30


def workspace_inventory(root: Path) -> tuple[str, ...]:
    """What lies in the workspace folder, read from disk at goal-choice time; facts only."""
    def files_in(d: Path) -> list[Path]:
        return [f for f in d.rglob("*")
                if f.is_file() and f.suffix != ".pyc"
                and not any(part in _INVENTORY_SKIP for part in f.relative_to(root).parts[:-1])]

    def kinds(files: list[Path]) -> str:
        exts: dict[str, int] = {}
        for f in files:
            key = f.suffix.lower() or "(no extension)"
            exts[key] = exts.get(key, 0) + 1
        return ", ".join(f"{n} {e}" for e, n in sorted(exts.items(), key=lambda x: -x[1])[:3])

    lines: list[str] = []
    try:
        tops = sorted(p for p in root.iterdir() if p.is_dir() and p.name not in _INVENTORY_SKIP)
    except OSError:
        return ()
    for top in tops:
        files = files_in(top)
        if not files:
            continue
        lines.append(f"{top.name}/ — {len(files)} files ({kinds(files)})")
        for sub in sorted(d for d in top.iterdir() if d.is_dir() and d.name not in _INVENTORY_SKIP):
            sub_files = files_in(sub)
            if sub_files:
                lines.append(f"  {top.name}/{sub.name}/ — {len(sub_files)} files ({kinds(sub_files)})")
    return tuple(lines[:_INVENTORY_LINES])


def unattended_tools(root: Path) -> tuple[str, ...]:
    """Tools open to an unattended run: registered minus closed."""
    import ast

    from core.autonomous_runtime import _AUTONOMOUS_GOAL_BLOCKED_TOOLS

    names: set[str] = set()
    for path in (root / "tools").glob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                            and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id == "name"
                            and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)):
                        names.add(stmt.value.value)
    return tuple(sorted(names - set(_AUTONOMOUS_GOAL_BLOCKED_TOOLS)))


def inventory_block(inventory: tuple[str, ...], tools: tuple[str, ...]) -> str:
    """Disk and open-tools inventory for a prompt: facts, no advice."""
    block = ""
    if inventory:
        block += ("\n\nWHAT IS IN YOUR WORKSPACE RIGHT NOW (listed from disk just before this "
                  "choice; a fact, not a suggestion):\n" + "\n".join(inventory))
    if tools:
        block += "\n\nTools an unattended run of yours can use: " + ", ".join(tools)
    return block
