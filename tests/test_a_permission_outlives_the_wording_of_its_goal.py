"""Разрешение переживает переформулировку цели, но не смену предмета.

WHY THIS EXISTS. Живой прогон 2026-09-17: в ящике девять висящих заявок, восемь
из них — `autonomous_runtime.allow_effects` об одном и том же, заведённые за сто
секунд. Ключи у всех разные:

    ain_2e13…  'Campaign goal: Create a reviewed proposal to split the oversized modul…'
    ain_a8b1…  'Campaign goal: Create a reviewed proposal to split core/self_build_pro…'
    ain_4052…  'Campaign goal: Turn the oversized module core/self_build_producer.py i…'

Ключ был `sha256` от ТЕКСТА цели, а текст цели хартия сочиняет моделью заново
каждый цикл. Тот же файл, тот же смысл, другая фраза — другой ключ — новая
заявка, и `_granted_effects_approval` не находит одобренное никогда. Человек
мог одобрять вечно: агент спрашивал снова.

Это MIR-072 в новом платье. Тот дефект (шестнадцать одинаковых заявок) закрыли
ключом; ключ работал, пока цель писали руками. Модель, пишущая цель заново, его
обошла, не сломав ни одного теста.

ГРАНИЦА, которую эти свидетели держат. Привязка к предмету ШИРЕ привязки к
фразе, и расширять её молча нельзя: одобрение на `core/a.py` не смеет разрешать
работу над `core/b.py`, а цель без названного файла обязана ключеваться
по-старому — иначе `test_the_key_is_still_scoped_by_goal` и
`test_the_key_that_asks_is_the_key_that_finds` потеряли бы смысл, не покраснев.
"""
from __future__ import annotations

import pytest

from core.autonomous_runtime import AutonomousRuntime

_WORDINGS = [
    ("Campaign goal: Create a reviewed proposal to split the oversized module "
     "core/self_build_producer.py into smaller components."),
    ("Campaign goal: Create a reviewed proposal to split "
     "core/self_build_producer.py into smaller components."),
    ("Campaign goal: Turn the oversized module core/self_build_producer.py "
     "into a set of smaller modules."),
]


@pytest.mark.parametrize("other", _WORDINGS[1:])
def test_one_subject_worded_twice_asks_permission_once(other: str) -> None:
    """Три формулировки прогона — один предмет, значит один ключ."""
    first = AutonomousRuntime._effects_dedup_key(_WORDINGS[0])

    assert first == AutonomousRuntime._effects_dedup_key(other), (
        "переформулировка цели снова завела бы новую заявку"
    )


def test_a_permission_for_one_file_is_not_a_permission_for_another() -> None:
    """Граница полномочий: предмет сужает право, а не отменяет его."""
    mine = AutonomousRuntime._effects_dedup_key("split core/self_build_producer.py")
    other = AutonomousRuntime._effects_dedup_key("split core/smart_memory.py")

    assert mine != other, "одобрение на один файл разрешило бы работу над другим"


def test_a_goal_that_names_no_file_is_still_keyed_by_its_words() -> None:
    """Откат: без названного предмета прежняя привязка к тексту цела.

    Без этого `test_the_key_is_still_scoped_by_goal` и
    `test_the_key_that_asks_is_the_key_that_finds` продолжали бы зеленеть на
    вырожденном ключе — обе цели там названы словами, без файлов.
    """
    goal = "найди и почини свои дефекты"

    assert (
        AutonomousRuntime._effects_dedup_key(goal)
        != AutonomousRuntime._effects_dedup_key(goal + " ещё")
    )


def test_the_key_still_names_the_operation_it_guards() -> None:
    """Приставка — часть договора: по ней заявку находят в ящике."""
    for goal in ("split core/self_build_producer.py", "цель без файла"):
        assert AutonomousRuntime._effects_dedup_key(goal).startswith(
            "autonomous_runtime.allow_effects:"
        )


def test_the_text_branch_cannot_forge_a_subject_key() -> None:
    """Ветви разведены приставкой, и подделать её текстом цели нельзя.

    Здесь я ошибся в первый раз и записываю это: сперва тест утверждал, что
    цель `"core/x.py"` обязана отличаться от `"split core/x.py"`. Он покраснел
    на моей же починке и был прав формально, а по сути неправ — цель, чей текст
    И ЕСТЬ путь, называет этот файл, значит это ОДИН предмет и одно право.
    Настоящая граница другая: попасть в пространство имён предмета, минуя
    предмет, нельзя — строка `"subject:core/x.py"` сама называет файл и потому
    ключуется предметом, а не своим текстом.
    """
    forged = AutonomousRuntime._effects_dedup_key("subject:core/x.py")

    assert forged == AutonomousRuntime._effects_dedup_key("split core/x.py")
    assert forged != AutonomousRuntime._effects_dedup_key("цель без файла")


def test_two_goals_about_one_file_share_one_permission() -> None:
    """Уступка названа вслух: предмет шире фразы, и это решение, а не случай."""
    assert (
        AutonomousRuntime._effects_dedup_key("split core/x.py")
        == AutonomousRuntime._effects_dedup_key("rewrite core/x.py")
    )
