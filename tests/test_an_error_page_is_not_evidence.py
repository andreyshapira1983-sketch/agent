"""Страница, чьё тело — сообщение об ошибке, уликой не становится.

ИСТОРИЧЕСКИЙ КЛАСС (H-01, журнал docs/audit/HISTORICAL_FAILURE_LEDGER.md).
Ariane 501, 4 июня 1996, отчёт комиссии Лионса: после отказа инерциальной
системы она выставила на шину ДИАГНОСТИЧЕСКИЙ код, а бортовой компьютер
прочитал его как ПОЛЁТНЫЕ ДАННЫЕ и отработал по нему рулями. Механизм не в
отказе датчика — датчик отказал честно. Механизм в том, что диагностика ушла
по каналу данных и была неотличима от данных.

ЛОКАЛЬНАЯ ФОРМА, ВОСПРОИЗВЕДЕНА 2026-08-23. Страница отвечает 200, а телом
отдаёт «404 Not Found», страницу входа, капчу или заглушку домена. Для
`web_fetch` это успех, для фабрики улик — `web_page` с доверием 0.75.

СЛЕДСТВИЕ, ЗАМЕРЕННОЕ, А НЕ ВЫВЕДЕННОЕ. Русское утверждение о предмете,
процитированное на такую английскую страницу, возвращалось **verified**:
страж алфавита (MIR-144) выключает проверку темы между языками — намеренно и
правильно, — и цитата разрешается. Это ежедневная форма работы агента: он
читает по-английски и отвечает по-русски. Английское утверждение при этом
демотировалось, то есть дыра открывалась ровно на самом частом пути.

ПОЧЕМУ ЧИНИТЬ НА СТОРОНЕ УЛИКИ, А НЕ УТВЕРЖДЕНИЯ. Судить утверждение — работа
цепочки гейтов верификатора, и второй судья над той же областью спорил бы с
первым. Здесь дефект в ПРОИЗВОДИТЕЛЕ: страница-ошибка не является источником
ни для какого утверждения, кем бы оно ни было. Отсечение на границе фабрики
улик оставляет вердикты в одних руках.

ГРАНИЦА. Настоящая страница ПРО ошибки (документация об HTTP 404, разбор
инцидента) обязана оставаться уликой. Разделяет их не слово, а длина: тело
заглушки коротко, статья — нет.
"""
from __future__ import annotations

import pytest

from core.evidence import evidence_from_tool_result

URL = "https://example.org/report"


def _evidence(text: str):
    return evidence_from_tool_result(
        tool_name="web_fetch",
        arguments={"url": URL},
        output={"url": URL, "text": text, "fetched_at": "2026-08-23T00:00:00Z"},
        status="success",
    )


@pytest.mark.parametrize("body", [
    "404 Not Found. The requested page does not exist on this server.",
    "403 Forbidden — access denied by the origin server.",
    "Service Unavailable. Please try again later.",
    "Sign in to continue. You must be logged in to view this content.",
    "Verify you are human. Enable JavaScript and cookies to continue.",
    "This domain is parked and available for purchase.",
    "Страница не найдена. Проверьте адрес и попробуйте снова.",
    "Доступ запрещён. Войдите в систему, чтобы продолжить.",
])
def test_an_error_body_does_not_become_evidence(body: str) -> None:
    assert _evidence(body) is None, (
        f"тело «{body[:44]}…» — сообщение об ошибке, а стало уликой web_page "
        "с доверием 0.75"
    )


def test_a_real_page_is_still_evidence() -> None:
    body = (
        "Выручка за квартал составила 20 миллионов рублей. "
        "Рост обеспечен продажами в северных регионах."
    )
    assert _evidence(body) is not None


def test_an_article_about_errors_is_still_evidence() -> None:
    """Граница: страница ПРО ошибки — законный источник.

    Разделяет не слово, а длина: заглушка коротка, разбор — нет.
    """
    body = (
        "404 Not Found is the status code a server returns when a resource "
        "does not exist. In this article we walk through why soft 404s are "
        "harmful for crawlers, how to distinguish them from hard 404s, and "
        "what the specification in RFC 9110 actually requires of an origin "
        "server. We then measure how common soft 404s are across a sample of "
        "ten thousand pages, and show that the majority of them originate "
        "from misconfigured single-page applications rather than from "
        "deliberate choices by site owners. The remainder of the article "
        "covers detection strategies and their false-positive rates."
    )
    assert _evidence(body) is not None, (
        "длинная статья про ошибки отвергнута — правило судит по слову, а не "
        "по форме страницы"
    )


def test_the_downstream_effect_is_gone() -> None:
    """Полная цепочка исторического класса: диагностика -> данные -> вердикт."""
    from core.evidence import ProvenanceChain
    from core.verifier import verify

    chain = ProvenanceChain()
    ev = _evidence("404 Not Found. The requested page does not exist on this server.")
    if ev is not None:  # до починки — улика создаётся
        chain.add(ev)

    report = verify(
        answer=f"Выручка за квартал составила 20 миллионов рублей. [web:{URL}]",
        chain=chain, llm=None, expects_contract_headers=False,
    )

    assert report.chunks[0].verdict != "verified", (
        "утверждение подтверждено страницей-ошибкой"
    )


def test_a_short_article_about_errors_is_still_evidence() -> None:
    """Вторая проверка границы, поставленная ПОСЛЕ подгонки.

    Первая версия правила судила по одной длине, и настоящая статья про
    soft-404 уложилась в 581 символ — то есть порог был подгонкой под фикстуру.
    Правило судит форму: заглушка коротка И состоит из считанных предложений.
    Этот случай короче предыдущего и всё равно обязан пройти.
    """
    body = (
        "404 Not Found is a status code. Crawlers treat it as a signal. "
        "Soft 404s break that signal. We measured ten thousand pages. "
        "Most came from misconfigured apps."
    )
    assert len(body) < 300, len(body)
    assert _evidence(body) is not None, (
        "короткая, но настоящая статья отвергнута — правило вернулось к длине"
    )
