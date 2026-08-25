"""Сигнал застоя остаётся наблюдательным, и теперь известно, чего это стоит.

Замер, отвергнутые варианты и границы: MIR-140 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

import ast
import inspect
import pathlib

from core.termination_guard import TerminationGuard


def test_the_guard_only_observes_and_never_stops() -> None:
    """Страж возвращает наблюдение; остановкой владеет политика перепланирования."""
    source = inspect.getsource(TerminationGuard)
    tree = ast.parse(source)

    raises = [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]
    assert not raises, (
        "страж застоя начал бросать — он получил власть останавливать ход, "
        "а измерение 2026-08-25 показало, что на наблюдаемом распределении "
        "он совпадает с бюджетами по видам отказов"
    )


def test_the_loop_does_not_break_on_stagnation() -> None:
    """Проводка границы: обнаружение застоя не прерывает цикл попыток.

    Проверяется САМ цикл, а не страж: наблюдательность стража ничего не
    значила бы, если бы вызывающий останавливался по его выводу.
    """
    repo = pathlib.Path(__file__).resolve().parent.parent
    src = (repo / "core" / "loop_attempt.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    stops: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.get_source_segment(src, node.test) or ""
        if "_stag" not in test_src:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, (ast.Break, ast.Return, ast.Raise)):
                stops.append(getattr(inner, "lineno", 0))

    assert not stops, (
        "цикл попыток стал прерываться по сигналу застоя — сигнал повышен до "
        f"управляющего действия, строки {stops}. Это отдельное решение: "
        "перечитайте MIR-140 и замер теневого режима"
    )


def test_the_shadow_still_records_where_a_stop_would_have_been() -> None:
    """Иначе июльское решение оператора теряет смысл: тень нужна ЧИТАЕМОЙ.

    Месяц её никто не читал, и ответ на вопрос «что стоила бы остановка»
    пролежал в журналах. Поле, в котором он живёт, обязано остаться.
    """
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "core" / "loop_attempt.py").read_text(encoding="utf-8")

    assert "_stagnation_shadow" in src
    assert "artifacts_at_detection" in src, (
        "из тени исчезло поле, по которому и считается цена остановки: "
        "что было В РУКАХ в момент срабатывания"
    )
