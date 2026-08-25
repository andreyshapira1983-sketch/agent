"""Сигнал застоя остаётся наблюдательным, и теперь известно, чего это стоит.

ПОЛЕВОЕ ОБНОВЛЕНИЕ 2026-08-25 (сводка оператора): «Bayesian Partner Modelling»
и ScienceFlow/ESTRA сходятся на том, что ценность даёт не знание об изменении
обстановки, а ПЕРЕХОД УПРАВЛЕНИЯ по этому знанию. У Bayesian триггер по
противоречию дал 2 перепланирования против 43/169/119 у периодического,
эвристического и судейского при сопоставимой награде.

У нас такой сигнал есть с 2026-07-27 и НАМЕРЕННО ничего не делает: решение
оператора было записывать, ГДЕ остановка произошла бы, чтобы сперва узнать,
что она стоила бы или сберегла. Месяц собранные улики никто не читал.

ПРОЧИТАНО 2026-08-25. Тень сработала ТРИ раза:

    попытка 2, plan_parse_failed x2, артефактов в руках: []
    попытка 2, plan_parse_failed x2, артефактов в руках: []
    попытка 3, verify_failed x2,     артефактов в руках: []

Ответ на июльский вопрос: остановка не стоила бы НИЧЕГО (в момент срабатывания
работы в руках не было ни разу) и не сберегла бы НИЧЕГО — во всех трёх случаях
существующие бюджеты по видам отказов остановили цикл на той же самой попытке.
`plan_parse_failed` и `verify_failed` имеют потолок 2, а глобальный потолок — 3.

ПОЭТОМУ СИГНАЛ НЕ ПОВЫШЕН ДО УПРАВЛЯЮЩЕГО. Не потому, что страшно, а потому,
что измерено: на наблюдаемом распределении он совпадает с уже действующим
правилом. Повышать его значило бы завести вторую власть над решением, которым
уже владеет `ReplanPolicy.decide`.

Тест закрепляет ГРАНИЦУ. Если сигнал однажды станет управляющим, это должно быть
отдельным решением с новым замером, а не побочным следствием правки цикла.
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
