"""Суточный снимок покрывает КАЖДОЕ хранилище и держит семь поколений.

Замер, отвергнутые варианты и границы: H-51 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

from scripts.snapshot_state import take_snapshot


def _workspace(tmp_path: pathlib.Path, *, stores: int = 3) -> pathlib.Path:
    data = tmp_path / "data"
    data.mkdir()
    for i in range(stores):
        (data / f"store_{i}.jsonl").write_text(f"строка {i}\n", encoding="utf-8")
    (data / "store_0.jsonl.old.bak").write_text("копия\n", encoding="utf-8")
    (data / "store_0.jsonl.lock").write_text("", encoding="utf-8")
    return tmp_path


def test_seven_generations_are_kept_and_the_eighth_falls_off(tmp_path) -> None:
    ws = _workspace(tmp_path)
    base = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    for day in range(9):
        take_snapshot(ws, now=base + timedelta(days=day))

    generations = sorted(
        p.name for p in (ws / "data" / "snapshots").iterdir() if p.is_dir()
    )
    assert len(generations) == 7, generations
    assert generations[0] == "20260803", "выпало не самое старое поколение"
    assert generations[-1] == "20260809"


def test_the_live_state_is_never_touched(tmp_path) -> None:
    """Граница: снимок читает живое и не трогает его — ни файла, ни строки."""
    ws = _workspace(tmp_path)
    before = {
        p.name: p.read_text(encoding="utf-8")
        for p in (ws / "data").glob("*.jsonl")
    }

    base = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    for day in range(9):
        take_snapshot(ws, now=base + timedelta(days=day))

    after = {
        p.name: p.read_text(encoding="utf-8")
        for p in (ws / "data").glob("*.jsonl")
    }
    assert after == before
