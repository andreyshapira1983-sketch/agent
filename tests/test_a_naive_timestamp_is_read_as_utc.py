"""Отметка без зоны читается как UTC, а не как местное время.

ИСТОРИЧЕСКИЙ КЛАСС (H-25, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
Тот же класс, что Mars Climate Orbiter (H-02), только во времени: две
конвенции и ни одной сквозной проверки между ними.

ЗАМЕР 2026-08-24. В живом состоянии наивных отметок НЕТ — 12 472 штуки, все с
зоной. Но механизм был жив: `datetime.fromisoformat("...T10:00:00")` без зоны
и `.astimezone(utc)` трактуют значение как МЕСТНОЕ, и на этой машине оно
становилось 07:00Z — тихий сдвиг на три часа. При сроке сиротства в 30 минут
живая задача мгновенно «осиротела» бы, то есть открылся бы класс двойного
исполнения, который MIR-033 измерил закрытым.

Из семи мест разбора, не приводивших значение к зоне, два сдвигали ТИХО
(`task_queue`, `scheduler`); остальные при сравнении падают громко, и это
приемлемо. Починены тихие.

ПОЧЕМУ UTC, А НЕ ОТКАЗ. Все писатели репозитория пишут UTC, так что наивное
значение может прийти только от правки руками или от нового писателя, и в
обоих случаях подразумевается UTC. Отказ читать такой файл остановил бы
очередь целиком из-за одной правки; приведение к UTC делает тихую ошибку
нулевой.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.task_queue import _parse_iso


@pytest.mark.parametrize(("raw", "expected"), [
    ("2026-08-24T10:00:00+00:00", datetime(2026, 8, 24, 10, tzinfo=timezone.utc)),
    ("2026-08-24T10:00:00Z", datetime(2026, 8, 24, 10, tzinfo=timezone.utc)),
    ("2026-08-24T10:00:00", datetime(2026, 8, 24, 10, tzinfo=timezone.utc)),
])
def test_all_three_forms_mean_the_same_instant(raw: str, expected: datetime) -> None:
    assert _parse_iso(raw) == expected, (
        f"«{raw}» прочитано как другой момент — вернулась трактовка наивного "
        "значения как местного времени"
    )


def test_an_offset_that_is_not_utc_is_still_converted() -> None:
    """Граница: явная зона обязана уважаться, а не подменяться на UTC."""
    assert _parse_iso("2026-08-24T13:00:00+03:00") == datetime(
        2026, 8, 24, 10, tzinfo=timezone.utc
    )


def test_the_live_state_carries_no_naive_stamps() -> None:
    """Замер, а не предположение: в хранилищах наивных отметок нет."""
    import json
    import pathlib
    import re

    keys = ("created_at", "updated_at", "completed_at", "started_at",
            "heartbeat_at", "run_after", "first_seen", "last_seen", "ts")
    naive = 0
    checked = 0
    for path in sorted(pathlib.Path("data").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            payload = row.get("payload", row)
            if not isinstance(payload, dict):
                continue
            for key in keys:
                value = payload.get(key)
                if not isinstance(value, str) or len(value) < 10:
                    continue
                checked += 1
                if not re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", value):
                    naive += 1

    assert checked > 100, "замер ничего не проверил — данные пропали?"
    assert naive == 0, f"наивных отметок в живом состоянии: {naive} из {checked}"
