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


@pytest.mark.parametrize("focus", _FABRICATED)
def test_every_fabricated_focus_area_is_dropped(focus: str):
    """Девять имён из девяти. Следующий прогон доставал их из памяти и шёл
    читать несуществующие файлы.
    """
    assert _checked_focus_area(focus, "repair") == ("", "monitor")


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
