"""Можно ли отдать прошлый результат шага вместо нового вызова.

Рабочая память хранит результат каждого шага; отдавать его вместо вызова можно
не всегда (2026-09-19: из кэша ушёл красный прогон тестов, снятый до правки).

Правило: прошлый результат годится, только если мир, который он описывает,
не мог измениться.

  * веб-источники — в пределах сессии, как и было (страница не наша);
  * file_read и find_in_files по одному файлу — пока у файла те же время изменения и размер;
  * python_probe — пока в ходе не было ни одного действия с планом отката (записи);
  * всё остальное (прогон тестов, поиск по папке, запись, время, память) — вызывается заново.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

_REMOTE_READS = frozenset({"web_search", "web_fetch", "semantic_scholar_search", "rss_fetch"})


def cache_stamp(tool_name: str, arguments: dict[str, Any], root: Path | None,
                effects: int | None = None) -> str | None:
    """Отпечаток состояния, при котором результат верен; None — не отдавать; effects — число записей."""
    if tool_name in _REMOTE_READS:
        return "remote"
    if tool_name == "python_probe":
        return None if effects is None else f"effects:{effects}"
    if tool_name not in ("file_read", "find_in_files") or root is None:
        return None
    raw = str((arguments or {}).get("path") or "").strip().replace("\\", "/")
    if not raw:
        return None
    target = Path(raw) if Path(raw).is_absolute() else Path(root) / raw
    if tool_name == "find_in_files" and not target.is_file():
        return None  # у папки время меняется только при добавлении и удалении, не при правке
    try:
        st = target.resolve().stat()
    except OSError:
        return None
    return f"{st.st_mtime_ns}:{st.st_size}"
