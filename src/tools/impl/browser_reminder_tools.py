"""
Инструменты: открыть ссылку в браузере, добавить напоминание.
Напоминания хранятся в data/reminders.json; при следующем сообщении в Telegram просроченные отправляются в чат.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from src.tools.base import tool_schema
from src.tools.registry import register

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_REMINDERS_FILE = _PROJECT_ROOT / "data" / "reminders.json"
_URL_SAFE = re.compile(r"^https?://[^\s<>]+$", re.I)


def _open_in_browser(url: str) -> str:
    """Открыть URL в браузере по умолчанию (Windows: Start-Process)."""
    u = (url or "").strip()
    if not u:
        return "Error: пустой URL."
    if not _URL_SAFE.match(u):
        return "Error: допустимы только ссылки вида https://... или http://..."
    try:
        import subprocess  # nosec B404
        subprocess.run(  # nosec B603
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", f'Start-Process "{u}"'],
            capture_output=True,
            timeout=10,
        )
        return f"Открыто в браузере: {u}"
    except FileNotFoundError:
        return "Error: powershell не найден."
    except subprocess.TimeoutExpired:
        return "Error: таймаут при открытии."
    except Exception as e:
        return f"Error: {e!s}"


def _parse_when(when_str: str) -> datetime | None:
    """Парсит фразу времени (локальное время): завтра 18:00, через 2 часа, 18:00 (сегодня)."""
    s = (when_str or "").strip().lower()
    if not s:
        return None
    now = datetime.now()
    # «через N час/часа/часов» / «через N мин»
    m = re.match(r"через\s+(\d+)\s*(час|часа|часов|ч|минут|мин|минуты)", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if "час" in unit or unit == "ч":
            return now + timedelta(hours=n)
        return now + timedelta(minutes=n)
    # «завтра 18:00» / «завтра в 18:00»
    if "завтра" in s:
        day = now.date() + timedelta(days=1)
        t = re.search(r"(\d{1,2}):(\d{2})", s)
        if t:
            hour, minute = int(t.group(1)), int(t.group(2))
            return datetime(day.year, day.month, day.day, hour, minute, 0)
        return datetime(day.year, day.month, day.day, 9, 0, 0)
    # «18:00» — сегодня
    t = re.search(r"(\d{1,2}):(\d{2})", s)
    if t:
        hour, minute = int(t.group(1)), int(t.group(2))
        out = datetime(now.year, now.month, now.day, hour, minute, 0)
        if out <= now:
            out += timedelta(days=1)
        return out
    return None


def _add_reminder(text: str, when_str: str) -> str:
    """Добавить напоминание. when_str: «завтра 18:00», «через 2 часа», «18:00»."""
    t = (text or "").strip()
    if not t:
        return "Error: текст напоминания пустой."
    at = _parse_when(when_str)
    if at is None:
        return f"Error: не удалось разобрать время «{when_str}». Примеры: завтра 18:00, через 2 часа, 18:00"
    _REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    reminders: list[dict] = []
    if _REMINDERS_FILE.exists():
        try:
            reminders = json.loads(_REMINDERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            reminders = []
    rid = f"rem_{int(at.timestamp())}_{len(reminders)}"
    reminders.append({
        "id": rid,
        "text": t[:500],
        "at": at.isoformat(),
    })
    _REMINDERS_FILE.write_text(json.dumps(reminders, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"Напоминание добавлено: «{t[:80]}{'…' if len(t) > 80 else ''}» на {at.strftime('%Y-%m-%d %H:%M')} (локальное время)."


def get_due_reminders() -> list[dict]:
    """Вернуть список просроченных напоминаний и удалить их из файла (сравнение по локальному времени)."""
    if not _REMINDERS_FILE.exists():
        return []
    now = datetime.now()
    try:
        reminders: list[dict] = json.loads(_REMINDERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    due = []
    kept = []
    for r in reminders:
        try:
            at_str = r.get("at", "")
            at = datetime.fromisoformat(at_str.replace("Z", "").replace("+00:00", "").strip()) if at_str else now
            if at <= now:
                due.append(r)
            else:
                kept.append(r)
        except Exception:
            kept.append(r)
    if due:
        _REMINDERS_FILE.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    return due


def register_browser_reminder_tools() -> None:
    register(
        "open_in_browser",
        tool_schema(
            "open_in_browser",
            "Open a URL in the default browser (Windows). Use for opening links for the user.",
            {"url": {"type": "string", "description": "Full URL (e.g. https://example.com)"}},
            required=["url"],
        ),
        _open_in_browser,
    )
    register(
        "add_reminder",
        tool_schema(
            "add_reminder",
            "Add a reminder; the user will receive it in Telegram when the time comes (on next message). Examples: 'завтра 18:00', 'через 2 часа', '18:00'.",
            {
                "text": {"type": "string", "description": "Reminder text (e.g. купить молоко)"},
                "when_str": {"type": "string", "description": "When to remind: завтра 18:00, через 2 часа, 18:00"},
            },
            required=["text", "when_str"],
        ),
        _add_reminder,
    )
