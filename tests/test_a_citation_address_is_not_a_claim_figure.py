"""Цифры в АДРЕСЕ цитаты не являются числами утверждения.

КАК НАШЛОСЬ. Оператор поручил измерить дискриминацию верификатора — то, чего
работа «Verify, Repair, Repeat, or Stop?» (arXiv 2607.17641) требует прежде
любого правила остановки. Замер парами (валидный ответ и его невалидный
близнец, отличающийся ровно одним элементом) дал ровную долю принятия
валидных 80 %, то есть каждый пятый ВЕРНЫЙ ответ отвергался. Ровность доли
и выдала систематическую причину.

ИЗОЛЯЦИЯ. Одно и то же предложение, поддержанное одним и тем же источником,
меняет вердикт от цифры в адресе цитаты:

    [web:…/p/2]    -> verified
    [web:…/p/20]   -> verified
    [web:…/p/0]    -> verified
    [web:…/p/abc]  -> verified
    [web:…/p/10]   -> topic_supported_but_claim_unverified
    [web:…/p/200]  -> topic_supported_but_claim_unverified
    [web:…/p/999]  -> topic_supported_but_claim_unverified

КОРЕНЬ. `extract_statistical_figures` получает текст куска ВМЕСТЕ с маркером
цитаты, поэтому число из пути адреса попадает в список чисел утверждения:
для `…20 миллисекунд. [web:…/p/10]` извлекается `['20', '10']`. Лишнего числа
в источнике нет, и кусок понижается до `topic_supported_but_claim_unverified`
с пометкой `[claim-figure-unverified]`. Односимвольные и нечисловые хвосты
проходят лишь потому, что не дотягивают до `_STAT_FIGURE_MIN_LEN`.

ЦЕНА. Адрес — не утверждение источника. Настоящие URL полны цифр: даты
(`/2026/08/23/`), идентификаторы статей, номера страниц. Каждый такой адрес
систематически понижал верно процитированное утверждение, а понижение входит
в `unverified_total` политики низкой доказанности и питает реплан по проверке
— то есть ложный отказ стоил и точности, и лишних ремонтов.
"""
from __future__ import annotations

import pytest

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import verify
from core.verifier_utils import extract_statistical_figures

SENTENCE = "Средняя задержка составила 20 миллисекунд."


def _verdict(url: str) -> str:
    chain = ProvenanceChain()
    chain.add(make_evidence(
        kind="web_page", source_id=url, obtained_via="web_fetch",
        claim="fetched page", excerpt=SENTENCE,
    ))
    report = verify(
        answer=f"{SENTENCE} [web:{url}]", chain=chain, llm=None,
        expects_contract_headers=False,
    )
    return report.chunks[0].verdict


@pytest.mark.parametrize("url", [
    "https://example.org/p/10",
    "https://example.org/p/200",
    "https://example.org/p/999",
    "https://example.org/news/2026/08/23/latency",
    "https://example.org/article/12345",
])
def test_digits_in_the_address_do_not_demote_a_supported_claim(url: str) -> None:
    assert _verdict(url) == "verified", (
        f"утверждение дословно повторяет источник и процитировано верно, но "
        f"понижено из-за цифр в адресе {url}"
    )


def test_the_address_never_becomes_a_claim_figure() -> None:
    """Корень, закреплённый отдельно от следствия."""
    with_citation = f"{SENTENCE} [web:https://example.org/p/10]"

    assert extract_statistical_figures(with_citation) == ["20"], (
        "число из пути адреса попало в числа утверждения"
    )


def test_a_figure_in_the_prose_is_still_extracted() -> None:
    """Граница, которую починка не имеет права перейти: числа самого
    утверждения обязаны извлекаться по-прежнему."""
    text = ("Доля выросла на 30 % против 12 % годом ранее. "
            "[web:https://example.org/p/77]")

    assert extract_statistical_figures(text) == ["30 %", "12 %"], (
        "числа самого утверждения перестали извлекаться — починка ложного "
        "отказа не имеет права ослеплять гейт"
    )


def test_a_wrong_figure_is_still_caught() -> None:
    """И главная граница: подменённое число обязано остаться пойманным —
    иначе починка ложного отказа купила бы ложное принятие."""
    url = "https://example.org/p/10"
    chain = ProvenanceChain()
    chain.add(make_evidence(
        kind="web_page", source_id=url, obtained_via="web_fetch",
        claim="fetched page", excerpt=SENTENCE,
    ))
    report = verify(
        answer=f"Средняя задержка составила 900 миллисекунд. [web:{url}]",
        chain=chain, llm=None, expects_contract_headers=False,
    )

    assert report.chunks[0].verdict != "verified", (
        "число, которого нет в источнике, принято как подтверждённое"
    )
