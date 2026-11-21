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


def test_a_browser_challenge_is_not_the_page() -> None:
    """Веб-экзамен 2026-09-19, второй прогон, N09: PyPI ответил 200 телом
    проверки браузера; по переносам строк в нём шесть «предложений», и
    счётчик предложений заглушку пропускал."""
    challenge = ("Client Challenge \n \n \n A required part of this site couldn’t load. This may be due to a browser\n"
                 " extension, network issues, or browser settings. Please check your\n"
                 " connection, disable any ad blockers, or try using a different browser.")
    assert _evidence(challenge) is None
    article = ("Client challenge-response authentication is described in RFC 7616. " * 20)
    assert _evidence(article) is not None, "a long article about challenges is still a source"
