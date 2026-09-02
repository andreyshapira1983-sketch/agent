"""Журнал изменений способностей: когда мир агента стал другим.

Зачем существует. Замер 2026-09-01/02: агент сам пометил своё исследование
незавершённым («вернуться, когда появится веб»), веб открыли — а страж
повторов отверг его возвращение: «ты уже говорил на эту тему». Система
наказала агента за правильное планирование незавершённой работы, потому что
у неё не было понятия «мир изменился»: recentness читалась как completion.

Здесь появляется машинный носитель этого понятия. Одна строка — одно
изменение состава заблокированных инструментов безнадзорного пути; одинаковый
состав строк не плодит. Страж повторов сравнивает время последней РАБОТЫ по
теме со временем последнего события: вердикт «здесь больше нечего делать»,
вынесенный в другом мире, не запирает тему в этом.
"""
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
    """Записать состав, если он изменился. True — событие легло.

    Журнал — не пульс: строка появляется только на ПЕРЕМЕНЕ. Сомнение
    (нечитаемый журнал) считается переменой — лучше лишний раз открыть тему,
    чем молча держать её запертой после настоящего изменения.
    """
    path = _path(workspace)
    current = sorted(blocked_tools)
    try:
        rows = read_state_jsonl_unlocked(path) if path.is_file() else []
        last = rows[-1].get("blocked_tools") if rows else None
    except (OSError, ValueError):
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
    """Время последнего изменения мира; "" — изменений не записано."""
    path = _path(workspace)
    if not path.is_file():
        return ""
    try:
        rows = read_state_jsonl_unlocked(path)
    except (OSError, ValueError):
        return ""
    # Первая строка — ТОЧКА ОТСЧЁТА, а не перемена: до неё состав мира не
    # записывался, и считать само появление журнала «изменением» значило бы
    # распечатать разом все завершённые темы (замерено свидетелем 2026-09-02).
    if len(rows) < 2:
        return ""
    return str(rows[-1].get("ts") or "")
