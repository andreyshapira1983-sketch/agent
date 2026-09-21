"""Скобка сверяется со СВОИМ числом, и пункты режутся только вне кода.

Разговор через мостик 2026-09-22: «там есть … три случая (вставка
`Edit[i, j−1]+1`, удаление `Edit[i−1, j]+1`, замена `Edit[i−1, j−1]` или
`+1`)» получило [claim-refuted] count_mismatch 3 против 6 — запятые ВНУТРИ
кода разрезали три пункта на шесть обрывков. Агент был прав (так у Эриксона).
Замер по всем ответам мостика: 4 срабатывания этой проверки, все 4 ложные.
"""
from __future__ import annotations

import pytest

from core.verifier_utils import enumeration_count_reason

_LIVE_FALSE_REFUTATIONS = [
    # запятые внутри кода
    ("там есть сигнатура `EditDistance(A[1 .. m], B[1 .. n])`, три случая (вставка "
     "`Edit[i, j−1]+1`, удаление `Edit[i−1, j]+1`, замена `Edit[i−1, j−1]` или `+1`)."),
    # скобка при ближнем числе, а сверялась с дальним
    ("Верхний уровень: 4 константы, 2 dataclass-модели (SplitStep, SplitPlan), "
     "1 класс-исключение (_NoHome) и 24 функции."),
    # разбивка по количествам — не перечень
    "шесть записей, из них три с явным полем severity (одна medium, две без значения).",
    # номер строки — не счёт
    ('В `core/verifier_patterns.py` строка 58 определяет `SELF_DECLARED_PREFIXES` как '
     '`frozenset({"general-knowledge", "judgement"})`.'),
]


@pytest.mark.parametrize("sentence", _LIVE_FALSE_REFUTATIONS)
def test_a_true_count_from_the_bridge_is_not_refuted(sentence: str) -> None:
    assert enumeration_count_reason(sentence) is None


@pytest.mark.parametrize(("sentence", "expected", "actual"), [
    ("Верхний уровень: 4 константы, 3 dataclass-модели (SplitStep, SplitPlan).", "3", "2"),
    ("три случая (вставка `Edit[i, j−1]+1`, удаление `Edit[i−1, j]+1`).", "3", "2"),
    ("Есть три позиции с qty меньше 6 (rotor-33: 5 шт., gasket-9: 0 шт.).", "3", "2"),
])
def test_a_false_count_is_still_caught(sentence: str, expected: str, actual: str) -> None:
    reason = enumeration_count_reason(sentence)
    assert reason is not None and (reason.expected, reason.actual) == (expected, actual)
