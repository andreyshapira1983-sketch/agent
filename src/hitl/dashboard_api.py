"""
Dashboard API: state, tasks, audit. MVP: placeholder (no HTTP server).
"""
from __future__ import annotations

from typing import Any


def get_state_for_dashboard() -> dict[str, Any]:
    from src.state.agent_state import get_state
    return get_state()


def get_audit_for_dashboard(n: int = 50) -> list[dict[str, Any]]:
    from src.hitl.audit_log import get_audit_tail
    return get_audit_tail(n)
