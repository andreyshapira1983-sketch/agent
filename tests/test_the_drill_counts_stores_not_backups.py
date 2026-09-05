"""Учение по восстановлению считает ХРАНИЛИЩА, а не копии.

Замер, отвергнутые варианты и границы: H-51 в docs/audit/HISTORICAL_FAILURE_LEDGER.md, H-36 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys


def test_the_drill_names_stores_without_a_backup(tmp_path) -> None:
    repo = pathlib.Path(__file__).resolve().parent.parent
    data = tmp_path / "data"
    data.mkdir()
    (data / "covered.jsonl").write_text("", encoding="utf-8")
    (data / "covered.jsonl.old.bak").write_text("", encoding="utf-8")
    (data / "naked.jsonl").write_text("", encoding="utf-8")

    proc = subprocess.run(  # noqa: S603 — свой модуль, аргументы не извне
        [sys.executable, str(repo / "scripts" / "restore_drill.py")],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
        env={"PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(repo),
             "SYSTEMROOT": r"C:\Windows", "PATH": ""},
        timeout=120, check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")

    assert "naked.jsonl" in out, (
        "хранилище без единой копии не названо — учение снова отвечает "
        "«хороши ли копии» вместо «что вообще можно восстановить»:\n" + out
    )
    assert "БЕЗ ЕДИНОЙ КОПИИ: 1" in out, out


def test_the_live_repository_coverage_is_stated(tmp_path) -> None:
    """Контроль: строка охвата обязана печататься и когда всё покрыто."""
    repo = pathlib.Path(__file__).resolve().parent.parent
    data = tmp_path / "data"
    data.mkdir()
    (data / "covered.jsonl").write_text("", encoding="utf-8")
    (data / "covered.jsonl.old.bak").write_text("", encoding="utf-8")

    proc = subprocess.run(  # noqa: S603 — свой модуль, аргументы не извне
        [sys.executable, str(repo / "scripts" / "restore_drill.py")],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
        env={"PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(repo),
             "SYSTEMROOT": r"C:\Windows", "PATH": ""},
        timeout=120, check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")

    assert "из них с копией: 1" in out, out
    assert "БЕЗ ЕДИНОЙ КОПИИ" not in out, out
