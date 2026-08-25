"""Учение по восстановлению считает ХРАНИЛИЩА, а не копии.

СВЕРКА С ПОЛЕМ, класс H-51 (docs/audit/HISTORICAL_FAILURE_LEDGER.md) —
многодневный отказ Atlassian, 2022. Первая часть класса (разрушительная команда
по неверным целям) закрыта H-36. Здесь третья и самая дорогая: восстановление
было рассчитано на ОДНУ единицу, а понадобилось для многих, и простой растянулся
на недели.

ЗАМЕР 2026-08-25. `scripts/restore_drill.py` перебирал КОПИИ и потому не мог
задать вопрос «у чего копий нет». Он показывал девять зелёных строк, тогда как
**19 живых хранилищ из 24 не имели ни одной копии** — среди них `budget_ledger`
(деньги), `approval_inbox` (полномочия) и `model_usage`. Каталог `data/`
исключён из git, поэтому версионный контроль запасным путём не является.

ОШИБКА БЫЛА В ПРИБОРЕ, А НЕ В ДАННЫХ. Проба перебирала не то множество, и её
зелёный цвет означал меньше, чем выглядел. Тест закрепляет именно это: пересчёт
идёт от списка хранилищ, и непокрытые обязаны быть НАЗВАНЫ, а не молча
пропущены.
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
             "SYSTEMROOT": "C:\Windows", "PATH": ""},
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
             "SYSTEMROOT": "C:\Windows", "PATH": ""},
        timeout=120, check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")

    assert "из них с копией: 1" in out, out
    assert "БЕЗ ЕДИНОЙ КОПИИ" not in out, out
