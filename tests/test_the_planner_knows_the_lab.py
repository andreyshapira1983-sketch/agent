"""Планировщик знает лабораторию: вердикт о СВОЕЙ среде меряется, не выводится.

Background: docs/CODE_NOTES.md, "The hand existed, the map did not show it".
"""
from __future__ import annotations

from core.planner_prompt import PLANNER_SYSTEM


def test_the_lab_is_in_the_planners_map():
    """Живые пробы 2026-08-16: инструмент был в реестре, но не в PLANNER_SYSTEM
    — а планировщик тянется только к тому, что есть в его карте мира. Проба 1:
    хотел python, белый список выбросил; проба 2: лаборатория есть — не взял.
    """
    assert "python_probe(" in PLANNER_SYSTEM


def test_the_map_teaches_measured_not_inferred():
    assert "MEASURED" in PLANNER_SYSTEM
    low = " ".join(PLANNER_SYSTEM.lower().split())
    assert "not inferred" in low or "не выводится" in low


def test_a_failing_experiment_is_taught_as_data():
    """Упавший эксперимент — удавшийся замер: без этой строки модель будет
    бояться планировать импорт, который «может упасть».
    """
    assert "SUCCESSFUL measurement" in PLANNER_SYSTEM


def test_the_runtime_self_boundary_is_kept():
    """Версию интерпретатора уже несёт <runtime_self> — лаборатория для того,
    чего runtime_self НЕ несёт: существования фич и сигнатур. Без границы
    планировщик жёг бы эксперимент на вопрос, отвеченный в подсказке.
    """
    idx = PLANNER_SYSTEM.find("python_probe(")
    section = PLANNER_SYSTEM[idx:idx + 2200]
    assert "runtime_self" in section
