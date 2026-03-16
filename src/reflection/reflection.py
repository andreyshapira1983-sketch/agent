"""
ReflectionEngine: анализ действий агента, self_assessment, лог и трассировка последовательности.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


class ReflectionEngine:
    def __init__(self) -> None:
        self.logs: list[dict[str, Any]] = []
        self.action_logs: list[dict[str, Any]] = []

    def record_action(self, action_name: str, context: Any = None) -> None:
        """Фиксирует действие агента с контекстом."""
        self.logs.append({
            "action": action_name,
            "context": context,
            "timestamp": datetime.utcnow(),
        })

    def analyze_action(self, action_data: dict[str, Any]) -> str:
        """Анализ одного действия. Возвращает 'Success' или 'Failure'."""
        success = action_data.get("success", False)
        prev_action = self.action_logs[-1]["action"] if self.action_logs else None
        last_error = self.action_logs[-1].get("error_count", 0) if self.action_logs else 0
        error_count = last_error + (0 if success else 1)
        self.action_logs.append({
            "action": action_data.get("action", ""),
            "success": success,
            "expected_result": action_data.get("expected_result"),
            "error_count": error_count,
            "previous_action": prev_action,
        })
        return "Success" if success else "Failure"

    def self_assessment(self) -> dict[str, float]:
        """Сводка: success_rate (0–100), error_rate (0–100), при наличии — error_count."""
        if not self.action_logs:
            return {"success_rate": 0.0, "error_rate": 0.0}
        n = len(self.action_logs)
        successes = sum(1 for e in self.action_logs if e.get("success"))
        errors = sum(1 for e in self.action_logs if not e.get("success"))
        total_errors = self.action_logs[-1].get("error_count", 0)
        out: dict[str, float] = {
            "success_rate": 100.0 * successes / n,
            "error_rate": 100.0 * errors / n,
        }
        if total_errors:
            out["error_count"] = float(total_errors)
        return out

    def get_sequence_trace(self) -> list[str]:
        """Последовательность имён действий в порядке вызова analyze_action."""
        return [e.get("action", "") for e in self.action_logs]
