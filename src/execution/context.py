"""
Execution context: current plan step, tool results, limits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass
class ExecutionContext:
    max_steps: int = 10
    step: int = 0
    results: list[Any] = field(default_factory=list)

    def can_continue(self) -> bool:
        return self.step < self.max_steps

    def advance(self) -> None:
        self.step += 1
