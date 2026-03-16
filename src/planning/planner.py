"""
Planner: build plan (list of steps).
MVP: rule-based + initiative question generator.
"""
from __future__ import annotations

import json
import os
import random
from collections import Counter
from typing import Any

from src.planning.schemas import Plan, PlanStep

# Глобальный счётчик упоминаний тем (для метрики интереса)
_topic_counter: Counter[str] = Counter()

# Пороговое число повторений, после которого смена темы считается «усиленной»
TOPIC_REPEAT_THRESHOLD = 3

_PLANNER_TOOLS = {
    "get_current_time",
    "fetch_url",
    "suggest_priority",
    "aggregate_simple",
    "parse_json",
    "get_my_inbox",
    "generate_question",
    "get_metrics",
    "get_reading_log",
}


def make_plan(goal: str) -> Plan:
    """Create plan from goal. Prefer LLM planner when available, fallback to rule-based."""
    _track_topic((goal or "").lower())
    llm_plan = _make_llm_plan(goal)
    if llm_plan is not None:
        return llm_plan
    return _make_rule_plan(goal)


def _make_rule_plan(goal: str) -> Plan:
    """Rule-based fallback planner."""
    steps: list[PlanStep] = []
    goal_lower = goal.lower()

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


def _make_llm_plan(goal: str) -> Plan | None:
    """
    LLM planner: генерирует 1-3 шага с инструментами.
    Если LLM недоступен/ошибка/плохой JSON — вернуть None и использовать rule-based fallback.
    """
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None
    try:
        client = OpenAI(api_key=key)
        prompt = (
            "Ты планировщик агента. Верни только JSON без markdown. "
            "Формат: {\"steps\":[{\"tool\":\"...\",\"arguments\":{}}]}\n"
            f"Доступные инструменты: {sorted(_PLANNER_TOOLS)}\n"
            "Правила: максимум 3 шага, минимум 1; только инструменты из списка; arguments всегда объект.\n"
            f"Цель: {goal}"
        )
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=250,
            timeout=25,
        )
        if not r.choices or not r.choices[0].message.content:
            return None
        raw = (r.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw.replace("json", "", 1).strip()
        data = json.loads(raw)
        raw_steps = data.get("steps") if isinstance(data, dict) else None
        if not isinstance(raw_steps, list):
            return None
        steps: list[PlanStep] = []
        for item in raw_steps[:3]:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or "").strip()
            if tool not in _PLANNER_TOOLS:
                continue
            args = item.get("arguments")
            if not isinstance(args, dict):
                args = {}
            steps.append(PlanStep(tool=tool, arguments=args))
        if not steps:
            return None
        return Plan(goal=goal, steps=steps)
    except Exception:
        return None


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
