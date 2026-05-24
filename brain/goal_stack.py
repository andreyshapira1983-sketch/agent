"""
brain/goal_stack.py — Goal Management

The Brain maintains a stack of goals.
Goals drive what context is built and what actions are taken.
LLM never writes to the goal stack directly — only the Brain does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(order=True)
class Goal:
    priority: int
    text: str = field(compare=False)
    status: str = field(default="active", compare=False)  # active | completed | failed

    def complete(self) -> None:
        self.status = "completed"

    def fail(self, reason: str = "") -> None:
        self.status = "failed"
        logger.warning("[GoalStack] Goal failed: %s | reason=%s", self.text, reason)


class GoalStack:
    """
    Ordered collection of goals.
    Higher priority = processed first.
    """

    def __init__(self) -> None:
        self._goals: list[Goal] = []

    def push(self, text: str, priority: int = 1) -> None:
        goal = Goal(priority=priority, text=text)
        self._goals.append(goal)
        self._goals.sort(reverse=True)  # highest priority first
        logger.info("[GoalStack] Pushed: '%s' (priority=%d)", text, priority)

    def current(self) -> list[dict]:
        """Return active goals as plain dicts (safe to pass to LLM context)."""
        return [
            {"text": g.text, "priority": g.priority, "status": g.status}
            for g in self._goals
            if g.status == "active"
        ]

    def depth(self) -> int:
        return len([g for g in self._goals if g.status == "active"])

    def update(self, result: Any) -> None:
        """
        Brain calls this after interpreting LLM output.

        - "stop"      → complete all active goals (session ending)
        - "respond"   → complete the highest-priority active goal
                        (Brain answered → goal fulfilled)
        - "tool_call" → do not auto-complete; tool may need multiple steps
        - other       → no change
        """
        action = getattr(result, "action", None)

        if action == "stop":
            for goal in self._goals:
                if goal.status == "active":
                    goal.complete()
            logger.info("[GoalStack] All goals marked complete (stop action)")

        elif action == "respond":
            # Complete the single highest-priority active goal
            active = [g for g in self._goals if g.status == "active"]
            if active:
                top = active[0]   # sorted by priority descending in push()
                top.complete()
                logger.info("[GoalStack] Goal completed on respond: '%s'", top.text)

    def clear(self) -> None:
        self._goals.clear()
        logger.info("[GoalStack] Cleared")
