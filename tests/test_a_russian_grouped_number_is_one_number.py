"""«48 750 руб.» — одно число, а не «48» и «750 руб» (24.09).

Живой прогон приёмки урока 1: сверка чисел резала сумму с пробелом между
разрядами на части и искала в улике «500 руб», а в Word стоит «Доставка 1 500»
— любое денежное утверждение по-русски получало «число не подтверждено».
Так же, как приклеенный знак валюты (23.09), единица при числе не обязательна
в улике: принимаются написания ТОГО ЖЕ числа, ни одного другого.
"""
from __future__ import annotations

import pytest

from core.verifier_utils import _excerpt_supports_figures, extract_statistical_figures

_DOCX = "Ноутбук-подставка 12 500\nКабель HDMI 3 750\nДоставка 1 500\nИТОГО: 48 750 руб."


@pytest.mark.parametrize("claim,figure", [
    ("В DOCX итог 48 750 руб.", "48 750 руб"),
    ("Доставка стоит 1 500 руб.", "1 500 руб"),
    ("Разница 1 500 руб.", "1 500 руб"),
])
def test_a_grouped_sum_is_extracted_whole(claim: str, figure: str) -> None:
    assert figure in extract_statistical_figures(claim)


@pytest.mark.parametrize("claim", [
    "В DOCX итог 48 750 руб.",
    "Доставка стоит 1 500 руб.",
    "Кабель HDMI — 3 750 руб., итог 48 750 руб.",
])
def test_the_same_sum_is_found_in_the_evidence(claim: str) -> None:
    assert _excerpt_supports_figures(_DOCX, extract_statistical_figures(claim))


@pytest.mark.parametrize("claim", [
    "В DOCX итог 46 000 руб.",
    "Доставка стоит 2 500 руб.",
])
def test_another_sum_is_still_not_found(claim: str) -> None:
    assert not _excerpt_supports_figures(_DOCX, extract_statistical_figures(claim))
