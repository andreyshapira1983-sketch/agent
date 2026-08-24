"""Квадратичные выражения безопасны лишь потому, что вход ограничен.

ИСТОРИЧЕСКИЙ КЛАСС (H-10, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
Cloudflare, 2 июля 2019: одно регулярное выражение в WAF с катастрофическим
возвратом съело процессор на всём периметре и уронило доступ глобально. Урок
не «регулярные выражения опасны», а «стоимость выражения умножается на размер
входа, и вход приходит снаружи».

ЗАМЕР 2026-08-24. Развёртка по 155 скомпилированным выражениям `core/`
патологическими входами: 14 медленнее 50 мс. Масштабирование трёх худших —
ровно **×4 на удвоение** входа (52 → 206 → 836 → 3381 мс на 1250 → 10000
символов), то есть КВАДРАТИЧНО, а не экспоненциально. Катастрофического
возврата нет; есть полиномиальная стоимость.

ПОЧЕМУ ЭТО ВСЁ РАВНО ЗАЩИТА, А НЕ ЗДОРОВЬЕ. Ограничивает не выражение, а
`MAX_EXCERPT_CHARS`: `make_evidence` усекает выдержку до 800 символов, и всё,
что работает ПОСЛЕ создания улики, видит только их. Худший случай падает до
7–21 мс. Но это предел, заведённый ради размера памяти, а не ради стоимости
разбора — то есть защита ПОБОЧНАЯ. Поднять его завтра ради «более полных
выдержек» будет выглядеть безобидно, и цена вернётся квадратично.

Поэтому граница закреплена здесь явно: тест краснеет, если предел вырастет
настолько, что худший случай перестанет быть дешёвым.
"""
from __future__ import annotations

import time

import pytest

from core.evidence import MAX_EXCERPT_CHARS, make_evidence
from core.topic_tokens import _COMPOUND_RE
from core.verifier_patterns import _STAT_TRIGGER_RE

#: Потолок стоимости одного разбора на предельной выдержке. Взят с запасом
#: втрое от замеренного худшего (21 мс), чтобы тест не дрожал от нагрузки
#: машины, но ловил рост предела: при 1600 символах цена станет вчетверо.
_WORST_CASE_BUDGET_MS = 70


@pytest.mark.parametrize(("name", "pattern", "filler"), [
    ("_STAT_TRIGGER_RE", _STAT_TRIGGER_RE, "1"),
    ("_COMPOUND_RE", _COMPOUND_RE, "я"),
])
def test_the_worst_case_stays_cheap_at_the_cap(name, pattern, filler) -> None:
    text = filler * MAX_EXCERPT_CHARS

    started = time.perf_counter()
    pattern.search(text)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < _WORST_CASE_BUDGET_MS, (
        f"{name} стоит {elapsed_ms:.0f} мс на предельной выдержке "
        f"({MAX_EXCERPT_CHARS} символов) — стоимость квадратична, значит "
        "вырос предел, а не выражение"
    )


def test_the_cap_is_actually_enforced_on_evidence() -> None:
    """Сама защита: без усечения потолок выше ничего не значит."""
    evidence = make_evidence(
        kind="web_page", source_id="u", obtained_via="web_fetch",
        claim="c", excerpt="я" * 50_000,
    )

    assert len(evidence.excerpt) <= MAX_EXCERPT_CHARS + 64, (
        "выдержка не усечена — квадратичная стоимость получила вход из сети "
        "без границы"
    )
