"""Agent brakes: files only the operator changes; write tools refuse them before approval and no approval lifts it."""
from __future__ import annotations

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
    """Which brake a write to `target` hits, or None; compared by resolved path so `./x//y` and symlinks are caught."""
    root = Path(workspace_root).resolve()
    resolved = Path(target).resolve()
    for rel in CONTROL_RELPATHS:
        guarded = (root / rel).resolve()
        if resolved == guarded or resolved == guarded.with_name(guarded.name + ".tmp"):
            return rel
    return None


def refuse_message(rel: str) -> str:
    return (f"refusing to write {rel}: it is an operator-only control file "
            "(kill switch, spend limits, self-repair cap, veto list, secrets); "
            "no approval unlocks it — ask the operator to change it")
