"""Отказ раскольщика ставит файл на паузу — до изменения его содержимого.

Замысел агента (разговор 2026-09-21, ~17:00–17:14): отказ `no_patch` не ставил
паузы, и производитель брал core/evidence_budget.py каждый цикл заново; машинный
отказ — не вердикт человека, ему свой журнал; выход — по отпечатку текста, не
по часам; вернули прежний текст — пауза вернулась. И второе (оператор видел
вживую): цель кампании трижды выбирала core/scheduler.py, который руки не брали
из-за отказа человека, — голова не видела паузы рук.
"""
from __future__ import annotations

from pathlib import Path

from core.splitter_refusals import record_refusal, refused_unchanged


def _ws(tmp_path: Path) -> Path:
    (tmp_path / "core").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "core" / "budget.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def test_a_refused_file_is_paused_until_its_text_changes(tmp_path) -> None:
    ws = _ws(tmp_path)
    assert refused_unchanged(ws) == frozenset()
    record_refusal(ws, "core/budget.py", "the proven group does not move whole")
    assert refused_unchanged(ws) == {"core/budget.py"}
    (ws / "core" / "budget.py").write_text("x = 2\n", encoding="utf-8")
    assert refused_unchanged(ws) == frozenset(), "изменённый файл выходит из паузы"
    (ws / "core" / "budget.py").write_text("x = 1\n", encoding="utf-8")
    assert refused_unchanged(ws) == {"core/budget.py"}, "прежний текст — прежняя пауза"


def test_a_missing_file_leaves_no_trace(tmp_path) -> None:
    ws = _ws(tmp_path)
    record_refusal(ws, "core/gone.py", "whatever")
    assert not (ws / "data" / "splitter_refusals.jsonl").exists()
