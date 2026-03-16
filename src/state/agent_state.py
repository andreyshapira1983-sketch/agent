"""
Agent state: current task, mode, context. MVP: simple dict.
"""
from __future__ import annotations

from typing import Any

_state: dict[str, Any] = {"mode": "idle", "current_plan": None, "agent_id": "root"}


def get_state() -> dict[str, Any]:
    return dict(_state)


def set_state(key: str, value: Any) -> None:
    _state[key] = value
