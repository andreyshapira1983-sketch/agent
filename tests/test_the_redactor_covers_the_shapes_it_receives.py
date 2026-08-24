"""Редактор обязан покрывать те формы, что реально доходят до журнала.

ИСТОРИЧЕСКИЙ КЛАСС (H-34, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
Heartbleed, 2014: ответ отдавал больше, чем спрашивали, потому что граница
проверялась не для всех форм запроса. Здесь то же в другом обличье: §7
объявляет, что три поверхности НИКОГДА не получают сырых секретов, а обход
полезной нагрузки покрывает не все её формы.

ЗАМЕР 2026-08-24, сквозной — не по коду, а по файлу. Секрет, положенный в
МНОЖЕСТВО или в БАЙТЫ, доходил до `logs/*.jsonl` сырым: логгер такие значения
принимает (приводит к строке), а редактор в них не заходил.

ЧЕГО ЭТОТ ТЕСТ НЕ ТРЕБУЕТ. Ключи словаря он не трогает. Там решение осознанное
и записано в докстринге `redact_payload`: ключи описывают схему, и переписать их
значит потерять журнал. Посылка проверена замером, а не принята на веру — 739
различных ключей в 6489 живых строках, и все до одного имена полей или
переменных окружения, ни одного «данными».

ЖИВЫХ СЛУЧАЕВ НЕТ: ни множеств, ни байтов в 6489 строках, и ноль
ключеподобных строк в 1269 файлах. Чинится потому, что заявленный инвариант
абсолютен, а починка стоит нескольких строк обхода и ничем не оплачивается.
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
