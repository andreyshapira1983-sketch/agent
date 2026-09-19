"""Можно ли отдать прошлый результат шага вместо нового вызова.

Рабочая память хранит результат каждого шага (он же — улика для следующих
ходов), и цикл отдавал его ВМЕСТО вызова при любом совпадении (инструмент,
аргументы) — без срока и без сброса. Замер 2026-09-19 (рабочий экзамен,
«почини по тестам заказчика»): агент верно исправил pricing.py, план прогонял
тесты снова, а цикл отдал красный прогон, снятый ДО правки; увидев красные
тесты, агент откатил верную правку. Утром тот же механизм отдавал старое
numbers.txt после того, как заказчик дописал строки между ходами.

Правило: прошлый результат годится, только если мир, который он описывает,
не мог измениться.

  * веб-источники — в пределах сессии, как и было (страница не наша);
  * file_read — пока у файла те же время изменения и размер;
  * всё остальное (прогон тестов, лаборатория, поиск и листинг по рабочей
    папке, запись, время, память) — вызывается заново: результат зависит от
    состояния, которое меняется, или само его меняет.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

_REMOTE_READS = frozenset({"web_search", "web_fetch", "semantic_scholar_search", "rss_fetch"})


def cache_stamp(tool_name: str, arguments: dict[str, Any], root: Path | None) -> str | None:
    """Отпечаток состояния, при котором результат верен; None — не отдавать из кэша."""
    if tool_name in _REMOTE_READS:
        return "remote"
    if tool_name != "file_read" or root is None:
        return None
    raw = str((arguments or {}).get("path") or "").strip().replace("\\", "/")
    if not raw:
        return None
    target = Path(raw) if Path(raw).is_absolute() else Path(root) / raw
    try:
        st = target.resolve().stat()
    except OSError:
        return None
    return f"{st.st_mtime_ns}:{st.st_size}"
