"""Reuse a previous step result only if the world cannot have changed.

* web sources: within the session;
* file_read and single-file find_in_files: while the file's mtime and size match;
* python_probe, list_dir, directory find_in_files: within the same turn, until a write;
* everything else (tests, writes, time, memory): run again.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

_REMOTE_READS = frozenset({"web_search", "web_fetch", "semantic_scholar_search", "rss_fetch"})
_EFFECT_STAMPED = frozenset({"python_probe", "list_dir"})


def _effects_stamp(effects: int | None, turn: int | None) -> str | None:
    if effects is None:
        return None
    return f"effects:{effects}" if turn is None else f"effects:{effects}:turn:{turn}"


def cache_stamp(tool_name: str, arguments: dict[str, Any], root: Path | None,
                effects: int | None = None, turn: int | None = None) -> str | None:
    """Fingerprint of the state under which a result is valid; None means do not reuse; effects = writes, turn = turn."""
    if tool_name in _REMOTE_READS:
        return "remote"
    if tool_name in _EFFECT_STAMPED:
        return _effects_stamp(effects, turn)
    if tool_name not in ("file_read", "find_in_files") or root is None:
        return None
    raw = str((arguments or {}).get("path") or "").strip().replace("\\", "/")
    if not raw:
        return None
    target = Path(raw) if Path(raw).is_absolute() else Path(root) / raw
    if tool_name == "find_in_files" and not target.is_file():
        return _effects_stamp(effects, turn) if target.is_dir() else None
    try:
        st = target.resolve().stat()
    except OSError:
        return None
    return f"{st.st_mtime_ns}:{st.st_size}"
