"""Log of capability changes: when the agent's world became different."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.state_integrity import (
    append_state_jsonl_unlocked,
    read_state_jsonl_unlocked,
    state_file_lock,
)

_RELPATH = Path("data") / "capability_events.jsonl"


def _path(workspace: str | Path) -> Path:
    return Path(workspace) / _RELPATH


def record_capability_snapshot(
    workspace: str | Path, blocked_tools: frozenset[str],
) -> bool:
    """Record the set of blocked tools if it changed; True means an event was written."""
    path = _path(workspace)
    current = sorted(blocked_tools)
    try:
        rows = read_state_jsonl_unlocked(path) if path.is_file() else []
        last = rows[-1].get("blocked_tools") if rows else None
    except (OSError, ValueError):
        # An unreadable journal counts as a change (the snapshot is appended, True returned):
        # better to reopen a topic once too often than keep it locked after a real change.
        last = None
    if last == current:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with state_file_lock(path):
        append_state_jsonl_unlocked(path, [{
            "ts": datetime.now(timezone.utc).isoformat(),
            "blocked_tools": current,
        }])
    return True


def last_capability_change_ts(workspace: str | Path) -> str:
    """Time of the last world change; empty string means none recorded."""
    path = _path(workspace)
    if not path.is_file():
        return ""
    try:
        rows = read_state_jsonl_unlocked(path)
    except (OSError, ValueError):
        return ""
    # The first line is a reference point, not a change: counting the log's appearance as a change would reopen every finished topic at once.
    if len(rows) < 2:
        return ""
    return str(rows[-1].get("ts") or "")
