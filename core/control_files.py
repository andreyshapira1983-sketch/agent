"""Agent brakes: files only the operator changes; write tools refuse them before approval and no approval lifts it."""
from __future__ import annotations

import os
from pathlib import Path

#: Paths relative to the workspace root, plus their sibling .tmp files (atomic writes go through `<path>.tmp` and replace).
CONTROL_RELPATHS: tuple[str, ...] = (
    "data/budget_kill_switch.json",
    "config/budget_limits.json",
    "data/patch_route_state.json",
    "config/vetoed_goals.txt",
    ".env",
    # Claude's decisions from the judge queue (core/judge_queue.py); the defendant does not write them.
    "data/judge_rulings.jsonl",
)


def control_file_hit(workspace_root: Path, target: Path) -> str | None:
    """Which brake a write to `target` hits, or None; by resolved path, folded relative path and identity (case-insensitive mounts, 8.3 names)."""
    root = Path(workspace_root).resolve()
    resolved = Path(target).resolve()
    try:
        folded = tuple(p.casefold() for p in resolved.relative_to(root).parts)
    except ValueError:
        folded = ()
    for rel in CONTROL_RELPATHS:
        guarded = (root / rel).resolve()
        for candidate in (guarded, guarded.with_name(guarded.name + ".tmp")):
            if resolved == candidate or _same_file(resolved, candidate):
                return rel
        rel_folded = tuple(p.casefold() for p in Path(rel).parts)
        if folded in (rel_folded, (*rel_folded[:-1], rel_folded[-1] + ".tmp")):
            return rel
    return None


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except (FileNotFoundError, NotADirectoryError):
        return False


def refuse_message(rel: str) -> str:
    return (f"refusing to write {rel}: it is an operator-only control file "
            "(kill switch, spend limits, self-repair cap, veto list, secrets); "
            "no approval unlocks it — ask the operator to change it")
