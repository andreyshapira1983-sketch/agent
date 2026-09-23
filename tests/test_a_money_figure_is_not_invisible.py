"""Денежное число не становится невидимым из-за написания.

Замер 2026-09-23 на живых следах: `low_evidence_policy` сработал шесть раз за
всю историю, и ПЯТЬ из шести стёртых ответов — экономическая работа (расчёт
окупаемости, выбор заказа, оценка ставки, разбор трёх заказов). От 590 до 4450
знаков за раз.

Причина не в пороге улик. Число утверждения сверялось с выдержкой улики
ДОСЛОВНО, а у денег дословного совпадения не бывает никогда:

* знак валюты извлекается вместе с числом («$0,171»), а инструменты печатают
  сумму словом («usd 0.171»);
* агент отвечает по-русски и пишет десятичную запятую, а инструменты печатают
  в C-локали точку.

Обе беды бьют ИМЕННО по деньгам, поэтому стирались именно экономические
ответы. Здесь заперты настоящие числа из стёртого ответа
(след trace_195ad14c5a0099d893c8da4314da4a7a, 2026-09-23 08:01) и настоящий
вывод опыта, который их посчитал.
"""
from __future__ import annotations

from core.verifier_utils import (
    _excerpt_supports_figures,
    _figure_variants,
    extract_statistical_figures,
)

#: Дословный вывод python_probe из того прогона.
PROBE_STDOUT = (
    "exit_code: 0\n"
    "stdout:\n"
    "per_call_usd 0.0043\n"
    "PDF-формуляры calls 40 usd 0.171\n"
    "QuickBooks сводная calls 25 usd 0.107\n"
    "контент-стратегия calls 30 usd 0.128\n"
    "rate 19 hours 6 net 114 orders_to_cover_700 6.1\n"
    "rate 25 hours 6 net 150 orders_to_cover_700 4.7\n"
    "rate 35 hours 6 net 210 orders_to_cover_700 3.3\n"
)


def test_a_money_figure_with_a_currency_sign_is_found() -> None:
    """«$0,171» и «usd 0.171» — одно число, а не два разных."""
    chunk = (
        "Один заказ стоит: PDF-формуляры — $0,171, QuickBooks сводная — $0,107, "
        "контент-стратегия — $0,128 [tool:python_probe]."
    )
    figures = extract_statistical_figures(chunk)
    assert figures, "числа утверждения должны извлечься"
    assert _excerpt_supports_figures(PROBE_STDOUT, figures)


def test_a_russian_decimal_comma_is_found_in_c_locale_output() -> None:
    chunk = "Повторный запуск обходится в 0,013 против 0,171 [tool:python_probe]."
    figures = extract_statistical_figures(chunk)
    excerpt = PROBE_STDOUT + "first_run_usd 0.171 second_run_usd 0.013\n"
    assert _excerpt_supports_figures(excerpt, figures)


def test_a_misquoted_figure_is_still_caught() -> None:
    """Сторож не ослаблен: 6,2 там, где опыт дал 6.1, не проходит.

    Это не оговорка, а смысл правки: принимаются другие НАПИСАНИЯ того же
    числа, но ни одно ДРУГОЕ число. В том самом ответе агент действительно
    переврал одно число из шести — и его ловить надо.
    """
    chunk = "При $19/ч × 6 ч нужно 6,2 заказа [tool:python_probe]."
    figures = extract_statistical_figures(chunk)
    assert not _excerpt_supports_figures(PROBE_STDOUT, figures)


def test_variants_never_invent_a_different_number() -> None:
    """Написания одного числа — не соседние числа."""
    for figure, forbidden in (
        ("$0,171", {"0.172", "0.17", "1.171"}),
        ("1,500", {"1.501", "150", "15000"}),
        ("6,1", {"6.2", "61.1"}),
    ):
        variants = _figure_variants(figure)
        assert variants
        assert not (variants & forbidden), (figure, variants & forbidden)


def test_thousands_separator_is_also_accepted() -> None:
    """Запятая может быть разделителем тысяч — оба чтения принимаются.

    Решать, что она значит, здесь не нужно и нельзя: «1,500» по-английски это
    1500, по-русски — 1,5. Принимаются оба, потому что ошибка в сторону
    лишнего вопроса дешевле стёртого ответа.
    """
    assert _excerpt_supports_figures("выручка 1500 usd", ["1,500"])
    assert _excerpt_supports_figures("ставка 1.5 usd", ["1,5"])
