"""Строгий разбор полей сохранённых записей: UTC-отметки и булевы флаги.

Одна копия на двух владельцев — очередь задач (`core/task_queue.py`) и
расписание (`core/scheduler.py`) читают свои JSONL-записи одними и теми же
правилами. Раньше каждый держал свою копию этих функций слово в слово; правка
одной (как H-25 ниже) обязана была повторяться во второй руками.
"""
from __future__ import annotations

from datetime import datetime, timezone

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).astimezone(timezone.utc).isoformat()


def parse_utc_iso(value: str) -> datetime:
    """Отметку без зоны считать UTC, а не местным временем.

    H-25 в docs/audit/HISTORICAL_FAILURE_LEDGER.md — класс «две конвенции, ни
    одной сквозной проверки» (Mars Climate Orbiter, только во времени).
    `datetime.fromisoformat("2026-08-24T10:00:00").astimezone(utc)` трактует
    наивное значение как МЕСТНОЕ и на этой машине даёт 07:00Z — тихий сдвиг на
    три часа. При сроке сиротства в 30 минут это значит, что живая задача
    мгновенно «осиротела», то есть открывается класс двойного исполнения,
    который MIR-033 измерил закрытым.

    В живом состоянии таких отметок нет: замер 2026-08-24 — 12 472 отметки, все
    с зоной. Механизм при этом жив, а все писатели репозитория пишут UTC,
    поэтому наивное значение приводится к UTC вместо местного: тихая ошибка
    становится нулевой, и ни одно чтение не падает на правке файла руками.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def strict_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
    raise ValueError(f"invalid boolean value: {value!r}")


def checked_iso_field(value: object, *, default: str) -> str:
    out = str(value or default)
    parse_utc_iso(out)
    return out
