"""Потоки torch считаются по квоте контейнера, а не по видимым ядрам (замер 2026-09-24)."""
from __future__ import annotations

from pathlib import Path

from core.memory_embeddings import cpu_budget


def test_a_container_quota_wins_over_visible_cores(tmp_path: Path) -> None:
    """Vast: видно 96, оплачено 23 — потоков 11, а не 96."""
    f = tmp_path / "cpu.max"
    f.write_text("2304000 100000\n", encoding="utf-8")
    assert cpu_budget(f) == 11


def test_no_quota_falls_back_to_visible_cores(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    f = tmp_path / "cpu.max"
    f.write_text("max 100000\n", encoding="utf-8")
    assert cpu_budget(f) == 4
    assert cpu_budget(tmp_path / "absent") == 4


def test_a_tiny_quota_still_gets_one_thread(tmp_path: Path) -> None:
    f = tmp_path / "cpu.max"
    f.write_text("50000 100000\n", encoding="utf-8")
    assert cpu_budget(f) == 1
