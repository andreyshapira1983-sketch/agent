"""Ворота домена сравнивают ИМЯ УЗЛА, а не сетевую часть адреса.

ИСТОРИЧЕСКИЙ КЛАСС (H-13, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
log4shell (CVE-2021-44228) — данные, истолкованные слоем форматирования.
Прямой формы у нас нет: шаблоны запросов лежат константами в модуле и снаружи
не приходят (развёртка нашла ровно два нелитеральных шаблона, оба — константы).
Но у класса есть соседняя, достижимая форма: тема запроса вставляется в
поисковую строку рядом с оператором `site:`, и её содержимое агент может
получить из скачанного текста.

ЗАМЕР 2026-08-24. Побег из запроса ВОЗМОЖЕН — «тема site:evil.example» и
«-site:wikipedia.org site:evil.example» проходят в строку поиска целиком. Но
второй слой держит: `allows_url` отвергает чужой домен, включая классическую
подмену суффиксом `wikipedia.org.evil.example` и побег через user-info
`https://wikipedia.org@evil.example/`. Слоёная защита работает, и защищает
именно ВТОРОЙ слой, а не построение запроса.

ЧТО БЫЛО НЕВЕРНО. `_domain` брал `netloc`, а он несёт и user-info, и ПОРТ.
Поэтому законный `https://wikipedia.org:8443/x` отвергался — ложный отказ, — а
побег через user-info блокировался по совпадению: строка `wikipedia.org@evil.example`
просто не оканчивается на разрешённый домен. Переход на `hostname` чинит порт
и делает случай с user-info верным ПО ПОСТРОЕНИЮ, а не по удаче.
"""
from __future__ import annotations

import pytest

from core.source_library import SOURCE_LIBRARY

_WIKI = next(e for e in SOURCE_LIBRARY if "wikipedia" in e.search_template)


@pytest.mark.parametrize(("url", "allowed"), [
    ("https://wikipedia.org/wiki/X", True),
    ("https://en.wikipedia.org/wiki/X", True),
    ("https://EN.WIKIPEDIA.ORG/wiki/X", True),
    ("https://wikipedia.org:8443/x", True),          # порт — тот же узел
    ("https://wikipedia.org@evil.example/x", False),  # user-info
    ("https://evil.example/?ref=wikipedia.org", False),
    ("https://wikipedia.org.evil.example/x", False),  # суффикс
    ("https://notwikipedia.org/x", False),            # префикс
])
def test_the_gate_decides_by_host(url: str, allowed: bool) -> None:
    assert _WIKI.allows_url(url) is allowed, url


def test_a_crafted_topic_cannot_reach_a_foreign_domain() -> None:
    """Полная цепочка класса: побег в запросе возможен, результат — нет."""
    query = _WIKI.query("тема site:evil.example")

    assert "site:evil.example" in query, (
        "тема больше не доходит до строки запроса — тогда этот тест мерит не то"
    )
    assert _WIKI.allows_url("https://evil.example/x") is False
