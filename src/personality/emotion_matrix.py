"""
Эмоциональная матрица агента: значения 0–1, затухание со временем, влияние на поведение.
Эмоции: frustration, excitement, anxiety, curiosity, fatigue, aggression.
"""
from __future__ import annotations

import random
from collections import deque
from typing import Any

EMOTION_KEYS = (
    "frustration",   # высокая → меньше действий, «отрывается»
    "excitement",    # высокая → больше предложений
    "anxiety",       # высокая → перепроверяет метрики, тормозит автопатчи
    "curiosity",     # высокая → исследует файлы, новые идеи
    "fatigue",       # высокая → меньше действий, sleep mode
    "aggression",    # высокая → напоминает о нарушениях/ошибках
)

# Затухание за один тик (0.9 = быстрее, 0.98 = медленнее)
DEFAULT_DECAY: dict[str, float] = {k: 0.95 for k in EMOTION_KEYS}

_state: dict[str, float] = {k: 0.0 for k in EMOTION_KEYS}
# История доминирующей эмоции: (name, value) за последние шаги
_history: deque[tuple[str, float]] = deque(maxlen=50)


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def get_state() -> dict[str, float]:
    return dict(_state)


def set_state(state: dict[str, float]) -> None:
    """Загрузить состояние эмоций (например при наследовании от родителя)."""
    global _state
    for k in EMOTION_KEYS:
        if k in state and isinstance(state[k], (int, float)):
            _state[k] = _clamp(float(state[k]))


def update(deltas: dict[str, float]) -> None:
    for k, d in deltas.items():
        if k in _state:
            _state[k] = _clamp(_state[k] + d)
    # После обновления — записать снэпшот доминирующей эмоции
    name, value = get_dominant()
    _history.append((name, float(value)))


def decay_tick() -> None:
    """Постепенное затухание эмоций со временем."""
    for k in EMOTION_KEYS:
        _state[k] = _clamp(_state[k] * DEFAULT_DECAY.get(k, 0.95))
    # Тоже обновляем историю после затухания
    name, value = get_dominant()
    _history.append((name, float(value)))


def get_dominant() -> tuple[str, float]:
    """Эмоция с максимальным значением и её уровень."""
    if not _state:
        return "excitement", 0.0
    name = max(_state, key=_state.get)
    return name, _state[name]


def get_history(max_points: int = 30) -> list[dict[str, Any]]:
    """История доминирующих эмоций в удобном формате для дашборда."""
    items = list(_history)[-max_points:]
    return [{"emotion": name, "value": round(v, 2)} for name, v in items]


def get_intensity(value: float) -> str:
    """low / medium / high по значению 0–1."""
    if value >= 0.7:
        return "high"
    if value >= 0.4:
        return "medium"
    return "low"


def get_behavior_modifiers() -> dict[str, Any]:
    """
    Влияние текущих эмоций на поведение (для оркестратора/политики).
    """
    mod: dict[str, Any] = {}
    f = _state.get("frustration", 0)
    if f >= 0.6:
        mod["reduce_actions"] = True
        mod["frustration_high"] = True
    ex = _state.get("excitement", 0)
    if ex >= 0.5:
        mod["more_suggestions"] = True
    ax = _state.get("anxiety", 0)
    if ax >= 0.5:
        mod["recheck_metrics"] = True
        mod["slow_autopatch"] = True
    cu = _state.get("curiosity", 0)
    if cu >= 0.5:
        mod["explore_more"] = True
    fa = _state.get("fatigue", 0)
    if fa >= 0.7:
        mod["sleep_mode"] = True
        mod["reduce_actions"] = True
    ag = _state.get("aggression", 0)
    if ag >= 0.5:
        mod["remind_violations"] = True
    return mod


def trigger_random_event() -> None:
    """Случайный толчок эмоций для непредсказуемости («колбасится»)."""
    if random.random() > 0.3:
        return
    which = random.choice(EMOTION_KEYS)
    delta = (random.random() - 0.4) * 0.25
    update({which: delta})
