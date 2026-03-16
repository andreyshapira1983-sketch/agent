"""
Planner: build plan (list of steps).
MVP: rule-based + initiative question generator.
"""
from __future__ import annotations

import random
from collections import Counter

from src.planning.schemas import Plan, PlanStep

# Глобальный счётчик упоминаний тем (для метрики интереса)
_topic_counter: Counter[str] = Counter()

# Пороговое число повторений, после которого смена темы считается «усиленной»
TOPIC_REPEAT_THRESHOLD = 3


def make_plan(goal: str) -> Plan:
    """Create a simple plan from goal. Can be extended with LLM later."""
    steps: list[PlanStep] = []
    goal_lower = goal.lower()

    # Обновить метрику интереса к теме
    _track_topic(goal_lower)

    if "время" in goal_lower or "time" in goal_lower or "который час" in goal_lower:
        steps.append(PlanStep(tool="get_current_time", arguments={}))
    elif "fetch" in goal_lower or "url" in goal_lower or "api" in goal_lower or "загрузить" in goal_lower:
        steps.append(PlanStep(tool="fetch_url", arguments={"url": "https://api.github.com"}))
    elif "priorit" in goal_lower or "очередь" in goal_lower or "порядок" in goal_lower:
        steps.append(PlanStep(tool="suggest_priority", arguments={"tasks_json": "[]"}))
    elif "aggregate" in goal_lower or "статистик" in goal_lower or "числа" in goal_lower or "stats" in goal_lower:
        steps.append(PlanStep(tool="aggregate_simple", arguments={"values_json": "[0]"}))
    elif "parse" in goal_lower or "json" in goal_lower:
        steps.append(PlanStep(tool="parse_json", arguments={"text": "{}"}))
    elif "inbox" in goal_lower or "сообщен" in goal_lower or "family" in goal_lower:
        steps.append(PlanStep(tool="get_my_inbox", arguments={}))
    elif "вопрос" in goal_lower or "ask" in goal_lower or "спрос" in goal_lower:
        steps.append(PlanStep(tool="generate_question", arguments={"context": goal}))
    elif "metric" in goal_lower or "монитор" in goal_lower or "quality" in goal_lower or "information" in goal_lower or "gather" in goal_lower:
        # Разнообразие: 2–3 шага, порядок случайный
        pool = [
            PlanStep(tool="get_current_time", arguments={}),
            PlanStep(tool="get_metrics", arguments={}),
        ]
        try:
            from src.tools.registry import get as reg_get
            if reg_get("get_reading_log"):
                pool.append(PlanStep(tool="get_reading_log", arguments={"limit": 3}))
        except Exception:
            pass
        random.shuffle(pool)
        steps.extend(pool[: 2 + (1 if random.random() > 0.5 else 0)])

    if not steps:
        steps.append(PlanStep(tool="get_current_time", arguments={}))
        steps.append(PlanStep(tool="get_metrics", arguments={}))

    return Plan(goal=goal, steps=steps)


# ------------------------------------------------------------------
# Метрика интереса к темам
# ------------------------------------------------------------------

def _track_topic(text: str) -> None:
    """Увеличить счётчик ключевых слов из запроса."""
    import re
    tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ]{4,}", text)
    stop = {"этот", "есть", "быть", "что", "как", "когда", "this", "that", "with"}
    for token in tokens:
        if token not in stop:
            _topic_counter[token] += 1


def get_topic_interest() -> list[tuple[str, int]]:
    """Вернуть топ тем по количеству упоминаний (убывающий порядок)."""
    return _topic_counter.most_common(10)
