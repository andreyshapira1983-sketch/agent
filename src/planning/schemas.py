"""
Planning schemas: plan step, plan state.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class PlanStep:
    tool: str
    arguments: dict[str, Any]
    status: StepStatus = StepStatus.PENDING
    result: str | None = None


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep]
    current_index: int = 0
