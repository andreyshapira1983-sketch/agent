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


@pytest.mark.parametrize("spelling", [
    r"split core\self_build_producer.py",
    "split ./core/self_build_producer.py",
    "split CORE/self_build_producer.py",
])
def test_one_file_spelled_two_ways_asks_permission_once(spelling: str) -> None:
    """Написание пути — тоже формулировка, и она тоже не вправе плодить заявки.

    Ревизия PR #343 нашла дыру ровно там, где я закрывал предыдущую: ключ стал
    предметным, но предмет берётся сырым. `_PY_TARGET_RE` — это
    `[\\w/\\\\.-]+\\.py`, то есть обратный слэш он ПРОПУСКАЕТ, а `_named_target`
    отдаёт совпадение дословно. Замер до починки: пять написаний одного файла
    дали ТРИ разных ключа. Заявленная цель этого PR — «человек одобряет один
    раз» — на двух из пяти написаний не выполнялась.

    Дефект мой и того же рода, что чинился: я переставил ключ с фразы на
    предмет и не спросил, сколько написаний у предмета.

    `./` здесь свидетель НЕ живого пути: `\\b` в начале регулярки словом
    границу не даёт, и точка в совпадение не попадает — эта строка сегодня
    зелена и до починки. Оставлена, чтобы нормализация была одна на все
    написания, а не заплата под два измеренных.
    """
    canonical = AutonomousRuntime._effects_dedup_key(
        "split core/self_build_producer.py"
    )

    assert AutonomousRuntime._effects_dedup_key(spelling) == canonical, (
        f"написание {spelling!r} завело бы отдельную заявку на тот же файл"
    )


def test_case_folding_the_key_does_not_fold_the_path_itself() -> None:
    """Граница уступки по регистру: свёрнут КЛЮЧ, а не путь к файлу.

    Складывать регистр в ключе — расширение: на POSIX `core/x.py` и `CORE/x.py`
    могут быть двумя разными файлами, и одно право накроет оба. Беру его
    сознательно, потому что рабочая область владельца — Windows, где это ОДИН
    файл, и потому что под этим правом стоит второй забор: сама заплата
    отдельно просит `self_apply_lane.run` с настоящим путём.

    Чего делать нельзя — сворачивать регистр там, где путь потом открывают.
    `_named_target` кормит `target_path` в `core/best_next_action.py`, и
    свёрнутый там регистр сломал бы поиск файла на POSIX. Поэтому регистр
    складывается ТОЛЬКО при счёте ключа.
    """
    from core.best_next_action_helpers import _named_target

    assert _named_target("split CORE/Smart_Memory.py") == "CORE/Smart_Memory.py"
    assert _named_target(r"split core\smart_memory.py") == "core/smart_memory.py"


def test_a_worded_goal_is_not_folded_by_case() -> None:
    """Сторож против перечинки: свёртка регистра не утекла в текстовую ветвь.

    Цель без названного файла ключуется текстом, и текст — это текст: две
    разные фразы, отличающиеся регистром, остаются двумя разными замыслами.
    """
    assert (
        AutonomousRuntime._effects_dedup_key("найди и почини свои дефекты")
        != AutonomousRuntime._effects_dedup_key("НАЙДИ и почини свои дефекты")
    )
