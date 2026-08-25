"""Редактор обязан покрывать те формы, что реально доходят до журнала.

Замер, отвергнутые варианты и границы: H-34 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import json

import pytest

from core.redaction import redact_payload

_SECRET = "sk-proj-ZZZZYYYYXXXXWWWWVVVVUUUUTTTTSSSS"


def _serialised(payload: object) -> str:
    """Ровно то, что увидит файл: логгер приводит несериализуемое к строке."""
    return json.dumps(redact_payload(payload), ensure_ascii=False, default=str)


@pytest.mark.parametrize(("name", "payload"), [
    ("строка", _SECRET),
    ("значение словаря", {"k": _SECRET}),
    ("список", [_SECRET]),
    ("кортеж", (_SECRET,)),
    ("множество", {"seen": {_SECRET}}),
    ("байты", {"raw": _SECRET.encode()}),
    ("глубина шесть", {"a": {"b": {"c": {"d": {"e": {"f": _SECRET}}}}}}),
])
def test_no_shape_carries_the_raw_secret_through(name: str, payload: object) -> None:
    assert _SECRET not in _serialised(payload), (
        f"форма «{name}» доносит сырой секрет до журнала"
    )


def test_the_probe_can_see_a_positive() -> None:
    """Контроль: без него зелёный набор значил бы лишь «сравнение не сработало»."""
    assert _SECRET in json.dumps({"k": _SECRET}, ensure_ascii=False)


def test_a_key_is_still_left_alone() -> None:
    """Граница: ключи остаются схемой, и это решение здесь не пересматривается.

    Тест закрепляет именно ГРАНИЦУ, а не дефект: если ключи однажды начнут
    редактироваться, это должно быть отдельным осознанным решением с новым
    замером живых ключей, а не побочным следствием правки обхода.
    """
    out = redact_payload({"schema_field": "значение"})
    assert "schema_field" in out

def test_the_real_logger_writes_no_raw_secret_for_these_shapes(tmp_path) -> None:
    """Не подражание логгеру, а сам логгер и сам файл.

    Прочие тесты этого файла сериализуют payload так же, КАК это делает логгер,
    и потому доказывают редактор, но не проводку. Здесь пишется настоящий
    журнал и читается настоящий файл: замер 2026-08-24 нашёл дефект именно так,
    и закрепляться он должен там же.
    """
    from core.logger import TraceLogger

    logger = TraceLogger("h34", log_dir=tmp_path, verbose=False)
    logger.log("probe_set", {"seen": {_SECRET}})
    logger.log("probe_bytes", {"raw": _SECRET.encode()})
    logger.log("probe_plain", {"text": _SECRET})

    body = (tmp_path / "h34.jsonl").read_text(encoding="utf-8")

    assert len([ln for ln in body.splitlines() if ln.strip()]) == 3, (
        "часть строк не записалась — тест мерил бы отсутствие записи, "
        "а не отсутствие утечки"
    )
    assert _SECRET not in body, "сырой секрет доехал до файла журнала"
    assert "REDACTED" in body, (
        "метки редакции нет вовсе — значит записалось что-то другое, "
        "и зелёный результат ничего не означает"
    )
