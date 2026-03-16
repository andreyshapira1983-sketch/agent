"""
Activity log: chronology of actions. MVP: in-memory list.
"""
from __future__ import annotations

from typing import Any
from collections import deque

_log: deque[dict[str, Any]] = deque(maxlen=500)


def log(action: str, details: dict[str, Any] | None = None) -> None:
    _log.append({"action": action, "details": details or {}})


def recent(n: int = 50) -> list[dict[str, Any]]:
    return list(_log)[-n:]
