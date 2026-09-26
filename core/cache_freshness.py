"""Прошлый результат шага отдаётся вместо вызова, только если мир не мог измениться.

  * веб-источники — в пределах сессии;
  * file_read и find_in_files по одному файлу — пока у файла те же время изменения и размер;
  * python_probe, list_dir, find_in_files по папке — в том же ходе, пока не было записи;
  * всё остальное (тесты, запись, время, память) — заново.
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
    """Отпечаток состояния, при котором результат верен; None — не отдавать; effects — записи, turn — ход."""
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
