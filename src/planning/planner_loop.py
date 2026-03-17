"""
Planner Loop: Observe → Think (LLM) → Select action → Execute.
Внутренние цели агента; LLM решает, что делать дальше при пустой очереди.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

_log = logging.getLogger(__name__)

# Мета-цели агента (п. 3a): что он может выбирать сам, без команды пользователя
INTERNAL_GOALS = [
    ("study_code", "изучать код, анализировать систему и улучшения"),
    ("update_knowledge", "обновлять внутреннюю базу знаний"),
    ("communicate_with_user", "общаться с пользователем (проактивное сообщение)"),
    ("create_tasks", "создавать новые задачи из плана улучшений"),
    ("run_maintenance", "метрики, здоровье системы"),
    ("continue_queue", "оставить выбор эмоциям/правилам (fallback)"),
]
# для обратной совместимости (orchestrator может проверять share_with_user)
LEGACY_ACTION_ALIASES = {"share_with_user": "communicate_with_user"}
ALLOWED_ACTIONS = {g[0] for g in INTERNAL_GOALS} | set(LEGACY_ACTION_ALIASES.keys())


def _state_summary_for_think(state: dict[str, Any]) -> str:
    """Краткое состояние для LLM (чтобы не перегружать контекст)."""
    parts = []
    m = state.get("metrics") or {}
    parts.append(f"Метрики: вызовов={m.get('calls', 0)}, успехов={m.get('successes', 0)}, ошибок={m.get('errors', 0)}.")
    a = state.get("self_assessment") or {}
    parts.append(f"Самооценка: success_rate={a.get('success_rate', 0)}%.")
    if state.get("inbox"):
        parts.append("Есть сообщения во inbox.")
    try:
        from src.tools.registry import call
        raw = call("get_reading_log", limit=1)
        if raw and "Лог прочитанного пуст" not in raw and "Записей нет" not in raw:
            first_line = raw.strip().split("\n")[0][:120]
            parts.append(f"Последнее прочитанное: {first_line}.")
    except Exception:
        pass
    return " ".join(parts)


def think_next_action(state: dict[str, Any]) -> tuple[str, str]:
    """
    LLM выбирает следующее действие по внутренним целям и состоянию.
    Учитывает ценность для целей и интересность (важность/новизна). Возвращает (action_id, reason).
    """
    summary = _state_summary_for_think(state)
    goals_text = "; ".join(f"{g[0]}=({g[1]})" for g in INTERNAL_GOALS)
    prompt = (
        f"Ты автономный агент. Текущее состояние: {summary}\n"
        f"Внутренние цели (выбери одну): {goals_text}\n"
        "Оцени выбор по: (1) ценности для текущих целей, (2) интересности (важность или новизна). "
        "Ответь СТРОГО JSON: {\"action\": \"<id>\", \"reason\": \"<одна короткая фраза>\"}. Только action из списка."
    )
    try:
        from openai import OpenAI
        key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_KEY_API", "")
        if not key:
            return "continue_queue", ""
        client = OpenAI(api_key=key)
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты планировщик. Учитывай ценность и интересность выбора. Отвечай только JSON: action, reason."},
                {"role": "user", "content": prompt[:2000]},
            ],
            max_tokens=150,
        )
        if not r.choices or not r.choices[0].message.content:
            return "continue_queue", ""
        raw = r.choices[0].message.content.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
        data = json.loads(raw)
        action = (data.get("action") or "continue_queue").strip().lower()
        if action in LEGACY_ACTION_ALIASES:
            action = LEGACY_ACTION_ALIASES[action]
        valid_ids = {g[0] for g in INTERNAL_GOALS}
        if action not in valid_ids:
            action = "continue_queue"
        reason = (data.get("reason") or "")[:200]
        return action, reason
    except Exception as e:
        _log.debug("think_next_action: %s", e)
        return "continue_queue", ""


def tasks_for_action(action_id: str) -> list[tuple[str, dict[str, Any], int]]:
    """
    По выбранному действию вернуть список (tool, arguments, priority) для постановки в очередь.
    """
    if action_id == "study_code":
        return [
            ("analyze_self_model", {}, 6),
            ("get_improvement_plan", {}, 5),
        ]
    if action_id == "update_knowledge":
        return [
            ("get_reading_log", {"limit": 5}, 6),
        ]
    if action_id in ("communicate_with_user", "share_with_user"):
        # Проактивное сообщение — в orchestrator (try_send_proactive), в очередь не ставим
        return []
    if action_id == "create_tasks":
        return [
            ("get_improvement_plan", {}, 6),
            ("suggest_priority", {"tasks_json": "[]"}, 5),
        ]
    if action_id == "run_maintenance":
        return [
            ("get_metrics", {}, 100),
            ("check_performance_alerts", {}, 5),
        ]
    # legacy
    if action_id == "read_books":
        return [("get_gutenberg_book_list", {"limit": 5, "start_index": 1}, 6)]
    if action_id == "analyze_system":
        return [("analyze_self_model", {}, 6), ("get_improvement_plan", {}, 5)]
    return []


def enqueue_planner_action(action_id: str, state: dict[str, Any]) -> int:
    """
    Поставить в очередь задачи, соответствующие действию из think_next_action.
    Возвращает число добавленных задач.
    """
    from src.tasks.queue import size, enqueue
    from src.tasks.task_state import Task
    import time
    if size() > 0:
        return 0
    specs = tasks_for_action(action_id)
    if not specs:
        return 0
    base = int(time.time() * 1000)
    added = 0
    for i, (tool, arguments, priority) in enumerate(specs):
        task_id = f"think_{base}_{i}_{tool}"
        task = Task(
            id=task_id,
            payload={
                "tool": tool,
                "arguments": arguments,
                "priority": priority,
                "depends_on": [],
            },
        )
        if enqueue(task):
            added += 1
        else:
            break
    return added
