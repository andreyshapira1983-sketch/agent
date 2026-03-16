"""
Эмоциональные триггеры: события обновляют матрицу эмоций.
"""
from __future__ import annotations

from src.personality.emotion_matrix import update, trigger_random_event

# Событие -> дельта по эмоциям (положительная = усиление)
TRIGGERS: dict[str, dict[str, float]] = {
    "cursor_unexpected": {"excitement": 0.15, "curiosity": 0.1},
    "cursor_broke": {"excitement": 0.05, "curiosity": 0.05, "frustration": 0.25},
    "user_sleep": {"aggression": -0.3, "excitement": -0.25, "fatigue": 0.2},
    "patch_failed": {"frustration": 0.2, "anxiety": 0.15},
    "patch_success": {"excitement": 0.1, "frustration": -0.05},
    "tool_slow": {"frustration": 0.1, "anxiety": 0.08},
    "performance_alert": {"anxiety": 0.12, "frustration": 0.05},
    "cycle_end_success": {"excitement": 0.08, "fatigue": 0.03},
    "cycle_end_fail": {"frustration": 0.12, "anxiety": 0.08},
    "user_praise": {"excitement": 0.15, "aggression": -0.1},
    "user_criticism": {"frustration": 0.1, "aggression": 0.08},
    "found_improvement": {"excitement": 0.12, "curiosity": 0.1},
    "autonomous_act": {"fatigue": 0.02},
    "rule_violation": {"aggression": 0.15, "frustration": 0.1},
}


def fire_trigger(event_name: str, with_random: bool = True) -> None:
    """Применить триггер по имени события; опционально добавить случайный толчок."""
    deltas = TRIGGERS.get(event_name)
    if deltas:
        update(deltas)
    if with_random:
        trigger_random_event()
