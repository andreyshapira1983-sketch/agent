"""
Audit log for human review. All sensitive changes logged for safety and rollback.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from collections import deque

_audit: deque[dict[str, Any]] = deque(maxlen=1000)


def audit(action: str, details: dict[str, Any]) -> None:
    _audit.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "details": details,
    })


def get_audit_tail(n: int = 100, action_filter: str | list[str] | None = None) -> list[dict[str, Any]]:
    """Last n entries, optionally only with action in action_filter (str or list)."""
    tail = list(_audit)[-n:]
    if not action_filter:
        return tail
    if isinstance(action_filter, str):
        action_filter = [action_filter]
    return [e for e in tail if e.get("action") in action_filter]


def format_audit_tail(n: int = 50) -> str:
    """Formatted string for tool result."""
    lines = []
    for e in get_audit_tail(n):
        ts = e.get("ts", "")[:19]
        lines.append(f"[{ts}] {e.get('action', '')}: {e.get('details', {})}")
    return "\n".join(lines) if lines else "No audit entries."
