"""What wakes a sleeping campaign: a change in the world, not the clock.

    Wake by event plus a rare fallback timer (`_BACKOFF_MAX_SECONDS`, unchanged).
    Events: `world_changed` (capability change journal, core/capability_events.py);
    `code_changed` (a new main-branch commit not authored by the agent itself, else
    it would wake itself); `file:<name>` (files from `AGENT_WAKE_FILES`, separated
    by `os.pathsep`: operator conversation, platform hiring; set by the server
    install because the paths live outside the agent's workspace). A new approval
    is checked by the loop itself, not here."""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.capability_events import last_capability_change_ts

WAKE_FILES_ENV = "AGENT_WAKE_FILES"
#: Author of the agent's own commits (core/safe_vcs.py, the self-repair lane).
AGENT_GIT_AUTHORS = frozenset({"Self-Apply Lane"})
_MAIN_BRANCHES = ("main", "master")


def _file_sig(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def _outside_head(workspace: Path) -> str | None:
    """Latest main-branch commit not by the agent; None if not a repository."""
    for branch in _MAIN_BRANCHES:
        try:
            cmd = ["git", "-C", str(workspace), "log", "-30", "--format=%H%x1f%an", branch, "--"]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)  # noqa: S603 — свой git, без оболочки
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            continue
        for line in out.stdout.splitlines():
            sha, _, author = line.partition("\x1f")
            if author not in AGENT_GIT_AUTHORS:
                return sha
        return ""
    return None


def outside_commit_ts(workspace: str | Path) -> str:
    """Time (ISO, UTC) of the latest foreign main-branch commit; "" if unknown."""
    sha = _outside_head(Path(workspace))
    if not sha:
        return ""
    try:
        out = subprocess.run(["git", "-C", str(workspace), "show", "-s", "--format=%cI", sha],  # noqa: S603, S607
                             capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    try:
        return datetime.fromisoformat(out.stdout.strip()).astimezone(timezone.utc).isoformat()
    except ValueError:
        return ""


def wake_files() -> list[Path]:
    raw = os.environ.get(WAKE_FILES_ENV, "")
    return [Path(p) for p in raw.split(os.pathsep) if p.strip()]


def wake_mark(workspace: str | Path) -> dict[str, Any]:
    """Snapshot of all wake sources, compared with the previous snapshot."""
    ws = Path(workspace)
    mark: dict[str, Any] = {"world_changed": last_capability_change_ts(ws),
                            "code_changed": _outside_head(ws)}
    for path in wake_files():
        mark[f"file:{path.name}"] = _file_sig(path)
    return mark


def woken_by(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Which source changed ("" if none). Order is the snapshot's order."""
    return next((key for key, value in after.items() if before.get(key, value) != value), "")
