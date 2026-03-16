"""
ReflectionEngine для learning: лог действий и исходов, анализ для вывода инсайтов.
"""
from __future__ import annotations

from typing import Any


class ReflectionEngine:
    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []
        self.action_log: list[dict[str, Any]] = []

    def log_action(self, action: Any) -> None:
        """Добавить запись о действии (legacy)."""
        self.actions.append(action)

    def record_action(self, action_type: str, context: dict[str, Any] | None = None, output: Any = None) -> None:
        """Записать действие с типом, контекстом и результатом."""
        self.action_log.append({
            "action_type": action_type,
            "context": context or {},
            "output": output,
            "outcome": None,
        })

    def record_outcome(self, action_index: int, outcome: str) -> None:
        """Записать исход для действия по индексу (1-based в тестах: 1 = первый)."""
        idx = action_index - 1 if action_index >= 1 else 0
        if 0 <= idx < len(self.action_log):
            self.action_log[idx]["outcome"] = outcome

    def analyze(self) -> list[str]:
        """Вернуть список текстовых инсайтов по записям с outcome."""
        insights = []
        for i, entry in enumerate(self.action_log):
            outcome = entry.get("outcome")
            if outcome is not None:
                num = i + 1
                insights.append(f"Action {num} was a {outcome}")
        return insights

    def generate_metrics(self) -> dict[str, Any]:
        """Сводка по действиям (legacy)."""
        return {
            "total_actions": len(self.actions),
            "last_action": self.actions[-1] if self.actions else None,
        }
