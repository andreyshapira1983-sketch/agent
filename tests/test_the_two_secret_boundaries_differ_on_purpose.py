"""Два рубежа секретов расходятся намеренно, и различие названо.

ИСТОРИЧЕСКИЙ КЛАСС (H-26, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
Дифференциал разборщиков — семейство, где два стража читают ОДИН вход и
приходят к разным выводам: от расхождений HTTP-парсеров в request smuggling до
классических случаев, когда фильтр и потребитель по-разному понимают одну
строку. Опасность не в строгости и не в мягкости, а в том, что расхождение
никем не заявлено.

ЗАМЕР 2026-08-24. Один текст, «My password is hunter2», проходит рубежи так:

    scan()                    -> 0 шаблонов
    contains_secret(kw=True)  -> True
    политика постоянной памяти-> reject
    redact_dlp_text()         -> ничего не вырезает
    эпизодическое хранилище   -> СОХРАНЯЕТ дословно

То есть один и тот же текст запрещён одному долговечному хранилищу и разрешён
другому.

ПОЧЕМУ ЭТО НЕ ЧИНИТСЯ ВЫРАВНИВАНИЕМ. Ключевой класс не находит ОТРЕЗКА: он
говорит «здесь речь о пароле», а не «вот пароль с такого по такой символ».
Вырезать нечего, поэтому редакция по нему действовать не может в принципе.
Рубеж, принимающий решение о ЗАПИСИ ЦЕЛИКОМ, им пользоваться может — и
пользуется. Разница в том, что каждый рубеж СПОСОБЕН сделать, а не в разной
строгости.

ПОЧЕМУ НЕ ЗАВЕДЕНЫ ВОРОТА НА ЭПИЗОД. Замер живых хранилищ: 6 426 строк
(эпизоды, постоянная память, реестр источников, журнал записей) — ноль
срабатываний и по шаблону, и по ключевому слову. Отклонять эпизоды по
ключевому слову значило бы терять запись всякого прогона, где обсуждалась
работа с паролями, — ложные отказы против нулевой измеренной частоты.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from core.memory_policy import MemoryWritePolicy
from core.redaction import redact_dlp_text
from core.secret_scanner import contains_secret, keyword_hits, scan

_KEYWORD_ONLY = "My password is hunter2 for the panel"


def test_the_keyword_class_finds_no_span_to_cut() -> None:
    """Корень различия, закреплённый отдельно от следствия."""
    assert scan(_KEYWORD_ONLY) == [], "шаблон нашёлся — пример перестал быть чистым"
    assert keyword_hits(_KEYWORD_ONLY), "ключевое слово не сработало"
    assert contains_secret(_KEYWORD_ONLY, include_keywords=True)[0] is True
    assert contains_secret(_KEYWORD_ONLY, include_keywords=False)[0] is False


def test_the_write_boundary_rejects_and_the_redactor_cannot() -> None:
    decision = MemoryWritePolicy().decide(_KEYWORD_ONLY, tags=("fact",))
    redacted, findings, _ = redact_dlp_text(_KEYWORD_ONLY)

    assert decision.decision == "reject", decision
    assert findings == [], "редакция нашла отрезок — значит различие исчезло"
    assert redacted == _KEYWORD_ONLY, (
        "текст изменён без найденного отрезка — это уже угадывание"
    )


def test_a_pattern_secret_is_caught_by_both() -> None:
    """Граница: расхождение допустимо ТОЛЬКО для ключевого класса."""
    key = "sk-proj-abcdef0123456789abcdef0123456789abcdef0123456789"

    assert MemoryWritePolicy().decide(key, tags=("fact",)).decision == "reject"
    redacted, findings, _ = redact_dlp_text(key)
    assert findings and key not in redacted


def test_the_live_stores_carry_neither_class() -> None:
    """Замер переснимается, а не вспоминается."""
    total = 0
    pattern_hits = 0
    keyword = 0
    for name in ("episodic_memory.jsonl", "persistent_memory.jsonl",
                 "source_registry.jsonl", "memory_writes.jsonl"):
        path = pathlib.Path("data") / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            total += 1
            if scan(line):
                pattern_hits += 1
            if keyword_hits(line):
                keyword += 1

    if total == 0:
        # Чистый клон (CI): data/ в gitignore, живых хранилищ нет — честный
        # пропуск; дома нулевое чтение — авария зонда. 2026-08-28.
        pytest.skip("нет живых хранилищ data/ в этом окружении")
    # Порог — защита от «зонд ничего не прочитал», а не размер одной
    # установки: здесь 781 строка при нуле находок (2026-09-19).
    assert total >= 100, "замер ничего не прочитал"
    assert pattern_hits == 0, f"секрет по шаблону в долговечном хранилище: {pattern_hits}"
    assert keyword == 0, (
        f"ключевой класс в долговечном хранилище: {keyword} из {total} — "
        "частота перестала быть нулевой, и решение «не заводить ворота» "
        "надо принимать заново"
    )


def test_json_shape_is_what_the_measurement_reads() -> None:
    """Контроль: замер читает строки файла, и они обязаны быть json."""
    path = pathlib.Path("data") / "episodic_memory.jsonl"
    if not path.exists():
        return
    first = next(
        (line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()),
        "",
    )
    assert json.loads(first)
