"""Дописанная строка состояния доходит до диска, а не только до кэша.

Замер, отвергнутые варианты и границы: H-50 в docs/audit/HISTORICAL_FAILURE_LEDGER.md, H-27 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import os

from core.state_integrity import append_state_jsonl, read_state_jsonl


def test_a_refused_barrier_does_not_lose_the_write(tmp_path, monkeypatch) -> None:
    """Граница: файловая система вправе отказать, и это не повод терять строку.

    Так же поступает путь атомарной перезаписи: отказ барьера оставляет запись
    ровно такой, какой она была до его появления, а не роняет вызов.
    """
    def _refuse(fd: int) -> None:
        raise OSError("файловая система отказала в барьере")

    monkeypatch.setattr(os, "fsync", _refuse)
    path = tmp_path / "s.jsonl"
    append_state_jsonl(path, [{"kind": "уцелевшая", "n": 7}])

    assert [r["kind"] for r in read_state_jsonl(path)] == ["уцелевшая"]


def test_an_empty_append_asks_nothing(tmp_path, monkeypatch) -> None:
    """Граница: пустой список — не работа, и барьера не заслуживает."""
    seen: list[int] = []

    def _spy(fd: int) -> None:
        seen.append(fd)

    monkeypatch.setattr(os, "fsync", _spy)

    append_state_jsonl(tmp_path / "s.jsonl", [])

    assert not seen
    assert not (tmp_path / "s.jsonl").exists()
