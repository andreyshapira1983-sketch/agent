"""
Agent spawner: request new agents only via Supervisor. Does not create agents directly.
Architecture: only supervisor creates agents; this module only sends spawn_request.
"""
from __future__ import annotations

from typing import Any

from src.agency import supervisor


def spawn_agent(
    task_spec: dict[str, Any] | str,
    depth: int = 0,
    parent_id: str | None = None,
    role: str = "",
    name: str = "",
) -> str | None:
    """
    Request a new agent for the task. Optional: parent_id, role, name (для семейки).
    Returns request_id if accepted, None if rejected.
    """
    return supervisor.request_spawn(task_spec, depth, parent_id=parent_id, role=role, name=name)


def get_pending_requests() -> list[dict[str, Any]]:
    """For Supervisor: list of pending spawn requests to process."""
    return supervisor.get_pending_spawn_requests()


def current_agent_count() -> int:
    return supervisor.current_agent_count()


def max_agents() -> int:
    return supervisor.MAX_AGENTS


def spawn_depth_limit() -> int:
    return supervisor.spawn_depth_limit()
