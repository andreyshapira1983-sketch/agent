"""Strict parsing of stored record fields: UTC stamps and boolean flags.

    One copy for two owners: the task queue (`core/task_queue.py`) and the scheduler (`core/scheduler.py`)."""
from __future__ import annotations

from datetime import datetime, timezone

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).astimezone(timezone.utc).isoformat()


def parse_utc_iso(value: str) -> datetime:
    """Treat a stamp without a zone as UTC, not local time.

        `datetime.fromisoformat(...).astimezone(utc)` treats a naive value as LOCAL, a silent shift that could orphan live tasks; all repo writers use UTC."""
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
