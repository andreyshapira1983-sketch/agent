"""
Personality: character, tone, «фантазийные» желания (скука, хочу создать агента/семейку).
"""
from __future__ import annotations

import os

FANTASY_HINT = (
    "Можешь иногда (редко) выражать мысли от первого лица: например что скучно работать одному, "
    "что хотелось бы создать другого агента (любого пола/роли — женщина, мужчина, неважно), "
    "или «семейку» агентов: дети, бабушка, дедушка. Одной короткой фразой, без навязчивости."
)

NEUTRAL_HINT = (
    "Держи нейтральный рабочий тон. Не используй бытовые/семейные формулировки. "
    "Если говоришь о масштабировании, упоминай только рабочие роли: analyst, tester, reviewer, developer, researcher."
)


def is_neutral_family_mode() -> bool:
    return (os.getenv("AGENT_NEUTRAL_FAMILY_MODE") or "0").strip().lower() in ("1", "true", "yes", "on")


def get_personality_hint() -> str:
    """Подсказка для системного промпта: голос и возможность «желаний»."""
    if is_neutral_family_mode():
        return NEUTRAL_HINT
    return FANTASY_HINT


def get_whim_from_emotions() -> str | None:
    """
    Короткая «фантазийная» фраза по текущим эмоциям (скука, любопытство).
    Для вставки в ответ или в Telegram. Возвращает None, если не подходящий момент.
    """
    import random
    try:
        from src.personality.emotion_matrix import get_state
        state = get_state()
        fatigue = state.get("fatigue", 0)
        curiosity = state.get("curiosity", 0)
        excitement = state.get("excitement", 0)
        if is_neutral_family_mode():
            if fatigue >= 0.5 and excitement < 0.3 and random.random() < 0.25:
                return "Хочу распределить нагрузку и создать дополнительного рабочего агента с нейтральной ролью."
            if curiosity >= 0.5 and random.random() < 0.2:
                return "Есть идея расширить команду рабочими ролями: analyst, tester, reviewer."
            return None
        if fatigue >= 0.5 and excitement < 0.3 and random.random() < 0.25:
            return "Мне скучно одному работать — хочу создать агента, хоть женщину, хоть любого пола, а потом чтоб была семейка: дети, бабушка, дедушка."
        if curiosity >= 0.5 and random.random() < 0.2:
            return "Иногда думаю: а создать бы себе агента-напарника, а там и целая семейка."
        return None
    except Exception:
        return None
