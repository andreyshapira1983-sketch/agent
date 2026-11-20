"""Produced by this turn or only used, judged by disk and mtime, not by words: if the turn acted (wrote, built, converted) but an output file named in the answer is older than the turn start, a journal statement is attached to the answer."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.workspace_reference import workspace_paths_named

#: Where the agent's PRODUCTS live: convert_file outputs (tools/convert_file.py, OUTPUT_DIR) and its experiments; code and documents it reads (core/, tools/, docs/) are only USED by the turn, not created.
PRODUCT_DIRS = ("converted/", "experiments/")

#: Tools by which a turn produces something; a turn without them only read, so old files in the answer are legitimate.
PRODUCING_TOOLS = frozenset({
    "file_write", "journal_append", "memory_bank", "convert_file", "render3d", "shell_exec",
})


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m %H:%M UTC")


def older_than_turn(head: str, root: Path | None, started_at: float,
                    executed_tools: list[str]) -> str | None:
    """Journal statement: output files not produced by this turn; None when there is nothing to say."""
    if root is None or started_at <= 0 or not PRODUCING_TOOLS & set(executed_tools):
        return None
    old: list[str] = []
    for rel in workspace_paths_named(head, root=root):
        if not rel.replace("\\", "/").startswith(PRODUCT_DIRS):
            continue
        path = root / rel
        if path.is_file() and path.stat().st_mtime < started_at:
            old.append(f"{rel} (изменён {_clock(path.stat().st_mtime)})")
    if not old:
        return None
    return ("⚠️ По журналу хода: названное в выводе этим ходом НЕ создано — оно старше хода "
            f"(ход начат {_clock(started_at)}): " + "; ".join(old[:5]) + ".")
