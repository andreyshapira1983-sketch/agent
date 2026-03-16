"""
Goal Manager: hierarchy, priorities, deferred intents. MVP: placeholder.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Goal:
    id: str
    description: str
    priority: int = 0
    deferred: bool = False


_goals: list[Goal] = []


def add_goal(description: str, priority: int = 0, deferred: bool = False) -> Goal:
    g = Goal(id=f"g_{len(_goals)}", description=description, priority=priority, deferred=deferred)
    _goals.append(g)
    return g


def get_current_goals() -> list[Goal]:
    return [g for g in _goals if not g.deferred]
