"""Редактирование ловит опасную половину пары, а не только публичную.

ИСТОРИЧЕСКИЙ КЛАСС (H-15, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
Cloudbleed, февраль 2017: ошибка разбора в HTML-парсере Cloudflare выводила
СОСЕДНЮЮ память в ответы, и чужие куки, токены и переписка оказывались в
кэшах поисковиков. Механизм — не «утечка секрета», а «в вывод попало то, что
рядом лежало».

ЗАМЕР 2026-08-24 по семи формам секретов. Две прошли насквозь:

* `AKIAIOSFODNN7EXAMPLE и wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY` — ловился
  идентификатор ключа, а СЕКРЕТ рядом уходил целиком. Публичен как раз
  идентификатор; опасна вторая половина.
* `https://user:p4ssw0rd-secret@example.org/x` — не находилось НИЧЕГО, хотя
  соседнее правило `mongodb-uri` знало ровно эту форму, но лишь для одной
  схемы. А скачивает адреса именно агент.

Достижимость прямая: `redact_dlp_text` чистит журналы, квитанции и карантин —
то есть места, которые переживают прогон и читаются потом.

ЗАМЕРЕННЫЙ ПРЕДЕЛ, записанный как предел. Голый 40-символьный секрет AWS БЕЗ
метки по-прежнему не ловится, и это намеренно: такая строка неотличима от
хеша или идентификатора, и правило по одной длине давало бы ложные
срабатывания на каждом sha. Ловится помеченная форма — та, в которой секреты
и утекают: конфиги, дампы окружения, json.
"""
from __future__ import annotations

import pytest

from core.secret_scanner import scan


@pytest.mark.parametrize(("label", "text", "kind"), [
    ("пароль в https", "https://user:p4ssw0rd-secret@example.org/x", "url-credentials"),
    ("пароль в postgres", "postgres://admin:hunter2@db.internal:5432/app", "url-credentials"),
    ("AWS с меткой", "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "aws-secret-key"),
    ("AWS в json", '{"aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"}', "aws-secret-key"),
])
def test_the_dangerous_half_is_found(label: str, text: str, kind: str) -> None:
    kinds = [f.kind for f in scan(text)]
    assert kind in kinds, f"{label}: найдено {kinds}"


@pytest.mark.parametrize("text", [
    "https://example.org/path/to/page?x=1",
    "https://example.org:8443/x",
    "commit a1b2c3d4e5f60718293a4b5c6d7e8f9012345678 прошёл",
    "hash=YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXoxMjM0NTY3OA==",
    "https://example.org/a:b@c/d",
])
def test_ordinary_text_stays_quiet(text: str) -> None:
    """Граница: каждое найденное здесь — карантин невиновного содержимого."""
    assert scan(text) == [], f"ложное срабатывание на: {text}"


def test_the_bare_aws_secret_remains_a_measured_limit() -> None:
    """Предел зафиксирован ЯВНО, чтобы его не приняли за защиту.

    Если однажды кто-то добавит правило по одной длине, этот тест покраснеет и
    заставит объяснить, чем оно отличается от совпадения с хешем.
    """
    text = "AKIAIOSFODNN7EXAMPLE и wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

    kinds = [f.kind for f in scan(text)]

    assert "aws-access-key" in kinds
    assert "aws-secret-key" not in kinds, (
        "голый секрет теперь ловится — правило изменилось, и надо доказать, "
        "что оно не ловит sha и идентификаторы"
    )
