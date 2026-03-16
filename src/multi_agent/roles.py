"""
Roles in multi-agent system. MVP: placeholder.
"""
from __future__ import annotations

_roles: dict[str, str] = {}


def register_role(name: str, agent_id: str) -> None:
    _roles[name] = agent_id


def get_agent_for_role(role: str) -> str | None:
    return _roles.get(role)
