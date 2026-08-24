"""Замок зависимостей полон: у каждого пакета есть хеш.

ИСТОРИЧЕСКИЙ КЛАСС (H-16, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
xz-utils, CVE-2024-3094 (март 2024): закладка лежала в выпускаемых архивах и
отсутствовала в репозитории. Предпосылка — то, что ЗАПУСКАЕТСЯ, отличается от
того, что рассматривали.

Здесь закрепляется свойство САМОГО ЗАМКА, а не окружения: каждый закреплённый
пакет обязан нести хотя бы один `--hash=sha256:`. Пакет без хеша выглядит
запертым по версии и при этом принимает любой архив с таким номером — то есть
даёт ровно ту разницу между «рассмотрели» и «поставили».

Расхождение УСТАНОВЛЕННОГО с замком тестом не проверяется намеренно: это
свойство машины, а не кода, и падение батареи от чужой установки было бы
ложным отказом. Для него есть `scripts/dependency_drift.py`, и на 2026-08-24
он сообщает два расхождения (`anthropic`, `click`) — запись в журнале H-16.
"""
from __future__ import annotations

import pathlib
import re

_LOCK = pathlib.Path(__file__).resolve().parents[1] / "requirements.lock"


def test_every_pinned_package_carries_a_hash() -> None:
    text = _LOCK.read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^(?=[A-Za-z0-9_.\-]+==)", text)

    missing = [
        match.group(1)
        for block in blocks
        if (match := re.match(r"([A-Za-z0-9_.\-]+)==", block))
        and "--hash=sha256:" not in block
    ]

    assert not missing, (
        f"пакеты заперты по версии, но без хеша: {missing} — такая строка "
        "примет любой архив с этим номером"
    )


def test_the_lock_is_not_empty() -> None:
    """Контроль: зелёный тест над пустым замком ничего бы не значил."""
    text = _LOCK.read_text(encoding="utf-8")

    assert text.count("--hash=sha256:") > 100, "замок подозрительно пуст"
