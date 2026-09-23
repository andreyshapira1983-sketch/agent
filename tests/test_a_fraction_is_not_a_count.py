"""Дробь — не счёт, слово-связка — не пункт, «и др.» — не полный перечень.

Замер 2026-09-23 на живом ответе агента: «coherence_score падает на 0.6 за
каждое противоречие высокой тяжести (например, планировщик…, а верификатор…)»
ушло человеку с клеймом claim-refuted — «ожидалось 6, насчитано 3». Строка
ИСТИННА (`_SEVERITY_WEIGHT['high'] == 0.6` в core/confidence_vector.py).
Точка резала предложение и заодно дробь 0.6, шестёрка становилась «обещанными
шестью пунктами», а «например» шло в счёт пунктов.

Прогон по 441 живому ответу (чат и эпизоды): до правки опровержений 11, после
— меньше, новых ноль; снятые прочитаны глазами — все были ложными. Сторож не
ослаблен: целое число против короткого перечня по-прежнему ловится (случай B1
«три позиции … две» держит test_counting_claims_and_cross_source_citations).
"""
from __future__ import annotations

import pytest

from core.verifier_utils import enumeration_count_reason

_LIVE = ("coherence_score падает на 0.6 за каждое противоречие высокой тяжести между "
         "подсистемами (например, планировщик говорит «готово», а верификатор — "
         "«полностью непроверено»).")


@pytest.mark.parametrize("sentence", [
    _LIVE,
    "балл падает на 0.6 за событие (первое, второе)",
    "балл падает на 0,6 за событие (первое, второе)",
    "назвал три плода (например, яблоко, груша, слива)",
    "лежит 41 текстовый файл с лекциями (Tong, Carroll, Srednicki, Preskill и др.).",
    "нашёл 12 статей (Шор, Белл, Эйнштейн и т. д.)",
])
def test_a_true_sentence_is_not_refuted(sentence: str) -> None:
    assert enumeration_count_reason(sentence) is None


@pytest.mark.parametrize(("sentence", "expected", "actual"), [
    ("он назвал 3 причины (первая, вторая)", "3", "2"),
    ("он назвал три причины (первая, вторая)", "3", "2"),
    ("Итог готов. Он назвал 3 причины (первая, вторая)", "3", "2"),
    ("назвал три плода (например, яблоко, груша)", "3", "2"),
])
def test_a_false_count_is_still_caught(sentence: str, expected: str, actual: str) -> None:
    reason = enumeration_count_reason(sentence)
    assert reason is not None and (reason.expected, reason.actual) == (expected, actual)
