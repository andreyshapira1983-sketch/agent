"""
Влияние эмоций на приоритет задач: оценка задачи для выбора следующей из очереди.
Чем выше score, тем раньше задачу стоит выполнить (эмоционально «подходящая»).
"""
from __future__ import annotations

from typing import Any

from src.personality.emotion_matrix import get_state, EMOTION_KEYS

# Инструмент -> веса по эмоциям. Положительный вес: при высокой эмоции эта задача предпочтительнее.
# Например: при высокой fatigue предпочитаем «лёгкие» задачи (get_metrics, get_current_time).
TOOL_EMOTION_WEIGHTS: dict[str, dict[str, float]] = {
    "get_metrics": {"fatigue": 0.6, "frustration": 0.2},
    "get_current_time": {"fatigue": 0.5},
    "suggest_priority": {"fatigue": 0.3, "excitement": 0.2},
    "analyze_tool_performance": {"anxiety": 0.4, "fatigue": 0.2},
    "check_performance_alerts": {"anxiety": 0.5},
    "export_performance_summary": {"anxiety": 0.3},
    "fetch_url": {"curiosity": 0.5, "excitement": 0.2},
    "parse_json": {"curiosity": 0.2},
    "aggregate_simple": {"fatigue": 0.2},
    "write_file": {"fatigue": -0.5, "anxiety": -0.4, "frustration": -0.2},
    "propose_file_edit": {"anxiety": -0.3, "frustration": -0.2},
    "apply_patch_with_approval": {"anxiety": -0.5, "fatigue": -0.4},
    "analyze_self_model": {"curiosity": 0.4, "excitement": 0.2},
    "get_improvement_plan": {"curiosity": 0.3, "excitement": 0.3},
    "update_prompt_rules": {"anxiety": -0.2, "excitement": 0.2},
    "manage_queue": {"fatigue": 0.2},
}


def get_task_priority_score(payload: dict[str, Any]) -> float:
    """
    Оценка приоритета задачи по текущему эмоциональному состоянию.
    Выше = предпочтительнее выполнить следующим (нагрузка «чувствуется»).
    """
    state = get_state()
    tool = (payload or {}).get("tool", "")
    weights = TOOL_EMOTION_WEIGHTS.get(tool)
    if not weights:
        return 1.0
    score = 1.0
    for emotion in EMOTION_KEYS:
        val = state.get(emotion, 0)
        w = weights.get(emotion, 0)
        score += val * w
    return max(0.01, score)
