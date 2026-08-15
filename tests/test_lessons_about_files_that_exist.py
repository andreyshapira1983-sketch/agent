"""Урок не вправе указывать на файл, которого нет.

Background: docs/CODE_NOTES.md, "Lessons about files that do not exist".
"""
from __future__ import annotations

import pytest

from core.reflection import _checked_focus_area

#: Дословно из десяти уроков, записанных двумя прогонами 2026-08-15.
_FABRICATED = [
    "core/reasoning.py",
    "core/citation.py",
    "core/user_contract.py",
    "core/logical_consistency.py",
    "core/obligation_management.py",
    "core/file_management.py",
    "core/obligation_tracking.py",
    "core/citation_management.py",
    "core/logical_coherence.py",
]

#: Настоящие модули тех же тем — правило обязано их пропускать.
_REAL = [
    "core/reasoning_action_check.py",
    "core/answer_contradiction.py",
    "core/completion_contract.py",
    "core/completion_obligation.py",
    "core/reflection.py",
]


@pytest.mark.parametrize("focus", _FABRICATED)
def test_every_fabricated_focus_area_is_dropped(focus: str):
    """Девять имён из девяти. Следующий прогон доставал их из памяти и шёл
    читать несуществующие файлы.
    """
    assert _checked_focus_area(focus, "repair") == ("", "monitor")


@pytest.mark.parametrize("focus", _REAL)
def test_a_real_file_keeps_its_lesson_intact(focus: str):
    """Улов не отдан: настоящий адрес — самое ценное, что есть в уроке."""
    assert _checked_focus_area(focus, "repair") == (focus, "repair")


@pytest.mark.parametrize("focus", [
    "general", "memory subsystem", "планирование", "",
])
def test_a_topic_name_is_not_a_path_and_is_not_checked(focus: str):
    """Не всякая область — адрес. Спрашивать у диска про «general» значило бы
    выбрасывать уроки за то, что они не про файл.
    """
    assert _checked_focus_area(focus, "repair") == (focus, "repair")


def test_a_repair_without_a_target_stops_being_a_repair():
    """Починка без цели не починка. Оставить `repair` значило бы хранить в
    памяти задачу, зовущую следующий прогон в пустоту.
    """
    assert _checked_focus_area("core/nope_xyz.py", "repair")[1] == "monitor"


def test_a_watch_or_a_study_keeps_its_own_action():
    """Понижается только `repair`: наблюдать и изучать можно и без адреса."""
    assert _checked_focus_area("core/nope_xyz.py", "monitor")[1] == "monitor"
    assert _checked_focus_area("core/nope_xyz.py", "learn_more")[1] == "learn_more"


@pytest.mark.parametrize("focus", [
    "core/subdir/made_up_module.py",
    "tools/invented_tool.py",
    "docs/NOT_A_REAL_DOC.md",
    "config/imaginary.json",
])
def test_unseen_fabrications_of_the_same_class_are_dropped(focus: str):
    """Формы, под которые правило не подгоняли: другие каталоги, другие
    расширения. Класс тот же — адрес, которого на диске нет.
    """
    assert _checked_focus_area(focus, "repair") == ("", "monitor")
