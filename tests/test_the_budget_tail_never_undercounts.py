"""Хвостовое чтение журнала бюджета не недосчитывает: ни из-за мусора в хвосте, ни из-за сбоя чтения."""
from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.budget_ledger import _TAIL_READ_BYTES, BudgetLedger, BudgetWindow

_NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
_LIMIT = 100
_IN_WINDOW = 60
_GARBAGE = [
    '{"counter": "llm_calls", "amo',
    '{"payload": [1]}',
    '{"counter": "llm_calls"}',
    "not json at all",
]
_NON_OBJECT_JSON = ["[1, 2, 3]", "42", '"text"']


def _ledger(tmp_path: Path) -> BudgetLedger:
    return BudgetLedger(
        path=tmp_path / "budget.jsonl",
        windows=[BudgetWindow(name="hour", seconds=3600, limits={"llm_calls": _LIMIT})],
    )


def _fill(ledger: BudgetLedger, *, garbage: list[str] | None = None) -> None:
    for i in range(2000):
        ledger.record("llm_calls", amount=1, reason="old", now=_NOW - timedelta(days=3, minutes=i))
    for i in range(_IN_WINDOW):
        ledger.record("llm_calls", amount=1, reason="recent", now=_NOW - timedelta(seconds=_IN_WINDOW - i))
        if garbage and i % 12 == 0:
            with ledger.path.open("a", encoding="utf-8") as fh:
                fh.write(garbage[(i // 12) % len(garbage)] + "\n")
    assert ledger.path.stat().st_size > _TAIL_READ_BYTES, "журнал должен быть длиннее одного хвоста"


def _used(ledger: BudgetLedger) -> int:
    decision = ledger.check("llm_calls", amount=_LIMIT - _IN_WINDOW + 1, now=_NOW)
    assert decision.allowed is False
    return decision.used


def test_garbage_lines_in_the_tail_do_not_lower_the_count(tmp_path: Path) -> None:
    """Мусорные и оборванные строки в хвосте пропускаются, настоящие записи окна считаются все."""
    ledger = _ledger(tmp_path)
    _fill(ledger, garbage=_GARBAGE)

    assert _used(ledger) == _IN_WINDOW


@pytest.mark.xfail(
    strict=True,
    raises=AttributeError,
    reason="BUG: строка журнала, которая разбирается как JSON, но не объект ([..], число, строка), "
    "роняет хвостовое чтение AttributeError (raw.get у не-dict), и check/reserve падают вместо "
    "решения — только когда журнал длиннее _TAIL_READ_BYTES [until: 2026-10-03 — владелец чинит]",
)
def test_a_non_object_json_line_in_the_tail_is_skipped_not_fatal(tmp_path: Path) -> None:
    """Строка JSON, которая не объект, в хвосте большого журнала пропускается, как при полном чтении."""
    ledger = _ledger(tmp_path)
    _fill(ledger, garbage=_NON_OBJECT_JSON)

    assert _used(ledger) == _IN_WINDOW


def test_a_failed_tail_read_falls_back_to_the_full_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой хвостового чтения ведёт к полному чтению, а не к недосчёту."""
    ledger = _ledger(tmp_path)
    _fill(ledger)
    real_open = pathlib.Path.open

    def _no_binary_read(self, mode="r", *args, **kwargs):
        if self == ledger.path and "b" in mode:
            raise OSError("simulated: tail read failed")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", _no_binary_read)

    assert _used(ledger) == _IN_WINDOW
