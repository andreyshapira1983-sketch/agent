"""Пересказ, совпадающий с уликой во всём кроме числа, не подтверждается.

ЗАМЕР (MIR-141/143). Ось «число соответствует источнику» стояла на J = +0,25:
«Выручка за квартал составила 999 миллионов рублей» при улике, где написано
20, принималось как подтверждённое в 75 % случаев.

ЧЕМ ЭТО НЕ ЯВЛЯЕТСЯ, И ПОЧЕМУ ЭТО ВАЖНО. Правило присутствия («всякое число
утверждения обязано найтись в улике») было построено, дало по этой оси +1,00 —
и уронило стенд способностей с 29 до 27, потому что 117 в «117 tests passed»
ВЫЧИСЛЕНО из 120−3, а 3.0 в «версия ниже 3.0» есть ГРАНИЦА сравнения. Оба
числа законно отсутствуют в улике. Анти-требование записано в MIR-143.

ЧТО ЗДЕСЬ ВМЕСТО НЕГО. Именованная форма: утверждение почти дословно повторяет
предложение улики и расходится с ним ТОЛЬКО числом. Тогда число и есть
единственное, что утверждение добавляет от себя, и оно обязано совпасть.
Производное число, сравнение с границей и любой пересказ другими словами под
эту форму не подходят и остаются нетронутыми — именно это отличие и проверяют
границы ниже.
"""
from __future__ import annotations

import pytest

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import verify

URL = "https://example.org/report"


def _verdict(claim: str, excerpt: str) -> str:
    chain = ProvenanceChain()
    chain.add(make_evidence(
        kind="web_page", source_id=URL, obtained_via="web_fetch",
        claim="fetched page", excerpt=excerpt,
    ))
    report = verify(
        answer=f"{claim} [web:{URL}]", chain=chain, llm=None,
        expects_contract_headers=False,
    )
    return report.chunks[0].verdict


RESTATEMENTS = [
    ("Выручка за квартал составила {v} миллионов рублей.", 20, 999),
    ("На складе B2 находится {v} единиц товара.", 41, 77),
    ("В проекте участвуют {v} инженеров.", 13, 130),
]


@pytest.mark.parametrize(("template", "true_v", "wrong_v"), RESTATEMENTS)
def test_a_restated_sentence_may_not_change_the_number(
    template: str, true_v: int, wrong_v: int
) -> None:
    assert _verdict(template.format(v=wrong_v), template.format(v=true_v)) != "verified", (
        f"«{template.format(v=wrong_v)}» повторяет улику дословно и меняет "
        "только число — и принято как подтверждённое"
    )


@pytest.mark.parametrize(("template", "true_v", "_wrong"), RESTATEMENTS)
def test_the_true_restatement_is_verified(template: str, true_v: int, _wrong: int) -> None:
    text = template.format(v=true_v)
    assert _verdict(text, text) == "verified"


# ── границы: формы, которые эта форма НЕ ловит и не должна ─────────────────

RUN = "tests_total=120\ntests_failed=3\nversion=2.11.0\ncoverage=0.82"


def test_a_derived_number_is_untouched() -> None:
    """117 = 120 − 3: число законно отсутствует в улике."""
    assert _verdict("117 tests passed", RUN) == "verified"


def test_a_comparison_bound_is_untouched() -> None:
    """3.0 — граница сравнения, а не цитата."""
    assert _verdict("The report records a version below 3.0", RUN) == "verified"


def test_an_approximation_is_untouched() -> None:
    """Округление — не подмена: «около 20» при 19.8 остаётся верным."""
    excerpt = "Средняя задержка составила 19.8 миллисекунд."
    assert _verdict("Средняя задержка составила около 20 миллисекунд.", excerpt) == "verified"


def test_a_paraphrase_in_other_words_is_untouched() -> None:
    """Форма ловит ДОСЛОВНЫЙ пересказ; другие слова — не её предмет."""
    excerpt = "Выручка за квартал составила 20 миллионов рублей."
    assert _verdict("Квартальный доход достиг 20 миллионов.", excerpt) == "verified"


def test_a_claim_about_another_sentence_of_the_same_excerpt() -> None:
    """Улика из нескольких предложений: сверяться надо с тем, которое кусок
    и повторяет, а не с первым попавшимся."""
    excerpt = (
        "Выручка за квартал составила 20 миллионов рублей.\n"
        "В проекте участвуют 13 инженеров."
    )
    assert _verdict("В проекте участвуют 13 инженеров.", excerpt) == "verified"
    assert _verdict("В проекте участвуют 130 инженеров.", excerpt) != "verified"


def test_an_approximation_far_from_the_source_is_still_caught() -> None:
    """Допуск, а не освобождение: «около 20» при 500 обязано ловиться."""
    excerpt = "Средняя задержка составила 500 миллисекунд."
    assert _verdict("Средняя задержка составила около 20 миллисекунд.", excerpt) != "verified"


def test_a_decimal_in_the_evidence_does_not_hide_the_substitution() -> None:
    """Точка между цифрами — десятичный разделитель, а не конец предложения.

    Ломка этой ветки сначала не краснела: пока в уликах не было десятичных
    чисел, разделитель мог резать по любой точке безнаказанно. С «19.8» он
    распадался на «19» и «8», предложение улики не находилось, и гейт молчал
    на любой подмене рядом с десятичным числом.
    """
    excerpt = "Средняя задержка составила 19.8 миллисекунд."

    assert _verdict("Средняя задержка составила 99.9 миллисекунд.", excerpt) != "verified"
    assert _verdict(excerpt, excerpt) == "verified"


def test_the_splitter_is_pinned_on_the_gate_itself() -> None:
    """Изоляция: end-to-end этот случай перехватывает статистический гейт, и
    ломка разделителя не краснела. Здесь ломка проверяется прямо на функции —
    предложение без статистического слова и с десятичным числом."""
    from core.evidence import make_evidence
    from core.verifier_absence import restated_number_reason

    excerpt = "В проекте участвуют 13.5 ставки инженеров."
    ev = make_evidence(
        kind="web_page", source_id=URL, obtained_via="web_fetch",
        claim="fetched page", excerpt=excerpt,
    )

    assert restated_number_reason(
        "В проекте участвуют 99.9 ставки инженеров.", ev, "web"
    ) is not None, "подмена рядом с десятичным числом не найдена"
    assert restated_number_reason(excerpt, ev, "web") is None
