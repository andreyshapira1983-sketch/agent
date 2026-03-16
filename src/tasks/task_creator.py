"""
Генерация задач агентом по себе: метрики, статус, эмоции.
Если очередь пустая — агент придумывает задачу (улучшить метрики, рефакторинг, обновить README и т.п.)
и кладёт в очередь через enqueue(). Связано с Orchestrator: на каждом цикле при пустой очереди вызывается try_generate_and_enqueue().
Поддерживаются: приоритет, зависимости (depends_on), эмоциональный скоринг, случайные «хотелки», уведомления в Telegram.
"""
from __future__ import annotations

import random
import time
from typing import Any

# (эмоция, вес для скоринга, инструмент, аргументы) — score = emotions[emotion] * weight; выбираем по убыванию score
_EMOTION_TASKS: list[tuple[str, float, str, dict[str, Any]]] = [
    ("curiosity", 1.0, "get_improvement_plan", {}),
    ("curiosity", 0.95, "get_gutenberg_book_list", {"limit": 5, "start_index": 1}),
    ("curiosity", 0.9, "analyze_self_model", {}),
    ("curiosity", 0.7, "list_pending_patches", {"emotion_aware": True}),
    ("excitement", 0.8, "get_improvement_plan", {}),
    ("fatigue", 0.8, "get_metrics", {}),
    ("fatigue", 0.6, "export_performance_summary", {}),
    ("anxiety", 0.9, "check_performance_alerts", {}),
    ("anxiety", 0.7, "analyze_tool_performance", {"top_n": 5}),
    ("frustration", 0.7, "get_metrics", {}),
    ("frustration", 0.6, "check_performance_alerts", {}),
]

# Мин. score для включения задачи в выбор (emotional task weight)
_EMOTION_SCORE_MIN = 0.15
_MAX_EMOTION_TASKS = 2

# Менее критичные «хотелки» — иногда добавляем одну в очередь (в т.ч. чтение/обучение без команды пользователя)
_WHIM_TASKS: list[tuple[str, dict[str, Any], int]] = [
    ("get_gutenberg_book_list", {"limit": 5, "start_index": 1}, 5),
    ("search_openlibrary", {"query": "programming", "limit": 5}, 5),
    ("get_my_family_tree", {}, 3),
    ("list_pending_patches", {"emotion_aware": True}, 4),
    ("get_current_time", {}, 2),
    ("suggest_priority", {"tasks_json": "[]"}, 3),
]
_WHIM_PROBABILITY = 0.25


def _emotional_task_score(emotion: str, weight: float, emotions: dict[str, float]) -> float:
    """Скор для одной кандидат-задачи: эмоция * вес."""
    return (emotions.get(emotion, 0) or 0) * weight


def _pick_tasks_from_emotions(state: dict[str, Any]) -> list[tuple[str, dict[str, Any], int]]:
    """
    По эмоциям выбрать до _MAX_EMOTION_TASKS задач с гибким скорингом (emotional task weight).
    Возвращает список (tool, arguments, priority 1–10).
    """
    try:
        from src.personality.emotion_matrix import get_state as get_emotion_state
        emotions = get_emotion_state()
    except Exception:
        emotions = {}
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for emotion, weight, tool, args in _EMOTION_TASKS:
        score = _emotional_task_score(emotion, weight, emotions)
        if score >= _EMOTION_SCORE_MIN:
            scored.append((score, tool, args))
    scored.sort(key=lambda x: -x[0])
    chosen: list[tuple[str, dict[str, Any], int]] = []
    seen_tools: set[str] = set()
    for _, tool, args in scored[: _MAX_EMOTION_TASKS + 2]:
        if tool in seen_tools:
            continue
        seen_tools.add(tool)
        priority = 7 if tool in ("check_performance_alerts", "get_metrics") else 5
        chosen.append((tool, args, priority))
        if len(chosen) >= _MAX_EMOTION_TASKS:
            break
    if not chosen:
        metrics = state.get("metrics") or {}
        errors = metrics.get("errors", 0)
        if errors > 0:
            chosen.append(("check_performance_alerts", {}, 8))
        else:
            chosen.append(("get_metrics", {}, 6))
    return chosen[: _MAX_EMOTION_TASKS]


def _maybe_add_whim() -> list[tuple[str, dict[str, Any], int]]:
    """С вероятностью _WHIM_PROBABILITY вернуть одну случайную «хотелку» (tool, args, priority)."""
    if random.random() >= _WHIM_PROBABILITY:
        return []
    w = random.choice(_WHIM_TASKS)
    return [(w[0], w[1], w[2])]


def try_generate_and_enqueue(state: dict[str, Any]) -> int:
    """
    Если очередь пустая — сгенерировать задачи по метрикам/эмоциям (со скорингом), опционально одну «хотелку».
    Кладёт в queue через enqueue(); отправляет в Telegram уведомление о сгенерированных задачах.
    Возвращает количество добавленных задач.
    """
    from src.tasks.queue import size, enqueue
    from src.tasks.task_state import Task
    if size() > 0:
        return 0
    specs: list[tuple[str, dict[str, Any], int]] = _pick_tasks_from_emotions(state)
    whim = _maybe_add_whim()
    for tool, args, prio in whim:
        if tool not in {s[0] for s in specs}:
            specs.append((tool, args, prio))
    if not specs:
        return 0
    base = int(time.time() * 1000)
    added = 0
    for i, (tool, arguments, priority) in enumerate(specs):
        task_id = f"self_{base}_{i}_{tool}"
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
    try:
        from src.communication.telegram_alerts import send_autonomous_event
        tools_str = ", ".join(s[0] for s in specs)
        if added > 0:
            send_autonomous_event(
                f"Агент добавил задачи в очередь: {tools_str}" + (f" ({added} из {len(specs)})" if added < len(specs) else ""),
                urgent=False,
                emotion_context={"source": "task_creator", "count": added},
            )
    except Exception:
        pass
    return added


def can_generate_task(state: dict[str, Any]) -> bool:
    """Проверка: могу ли сгенерировать задачу (очередь пустая и есть что предложить)."""
    from src.tasks.queue import size
    if size() > 0:
        return False
    return len(_pick_tasks_from_emotions(state)) > 0


def enqueue_task_with_dependency(
    tool: str,
    arguments: dict[str, Any],
    depends_on: list[str],
    priority: int = 5,
) -> Task:
    """
    Добавить в очередь одну задачу с явными зависимостями (для сложных цепочек).
    Возвращает созданную Task.
    """
    from src.tasks.queue import enqueue
    from src.tasks.task_state import Task
    task_id = f"chain_{int(time.time() * 1000)}_{tool}"
    task = Task(
        id=task_id,
        payload={
            "tool": tool,
            "arguments": arguments,
            "priority": priority,
            "depends_on": list(depends_on),
        },
    )
    if not enqueue(task):
        raise RuntimeError("Task queue full or max tasks per cycle reached. Complete tasks before adding.")
    return task
