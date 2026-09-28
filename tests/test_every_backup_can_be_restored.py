"""Каждая резервная копия читается настоящим загрузчиком.

ИСТОРИЧЕСКИЙ КЛАСС (H-12, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
GitLab.com, 31 января 2017: рабочий каталог основной базы удалён во время
восстановления, и из ПЯТИ способов резервирования не сработал ни один. Отчёт
называет причину прямо: копия, восстановление из которой никто не пробовал, —
не копия, а предположение.

Здесь предположение превращается в замер: каждая копия в `data/` прогоняется
через тот же `read_state_jsonl`, что читает живые файлы, со всеми контрольными
суммами. Замер 2026-08-24: девять копий, читаются девять.

Отдельно проверяется, что учение вообще способно ПОЙМАТЬ битую копию —
иначе зелёный тест означал бы только отсутствие копий.
"""
from __future__ import annotations

import pathlib
import shutil

import pytest

from core.state_integrity import quarantine_dir_for, read_state_jsonl

_BACKUPS = sorted(pathlib.Path("data").glob("*.bak"))


@pytest.mark.skipif(not _BACKUPS, reason="в этом клоне резервных копий нет")
@pytest.mark.parametrize("backup", _BACKUPS, ids=lambda p: p.name[:40])
def test_a_backup_loads_through_the_real_loader(backup: pathlib.Path, tmp_path) -> None:
    """Каждый непустой ряд копии читается как полезная нагрузка; карантин пуст.

    Читается копия в tmp: на битых рядах загрузчик переписывает файл, который читает."""
    copy = tmp_path / backup.name
    shutil.copyfile(backup, copy)
    expected = sum(1 for line in backup.read_bytes().split(b"\n") if line.strip())

    rows = read_state_jsonl(copy)

    assert not quarantine_dir_for(copy).exists(), f"{backup.name}: ряды ушли в карантин"
    assert len(rows) == expected, backup.name
    assert not any("_integrity" in row for row in rows), backup.name


def test_the_drill_has_a_case_even_where_data_is_absent(tmp_path) -> None:
    """Блок 5 (аудит G7, 2026-09-03): `data/` исключён из git, и в CI учение
    собирало НОЛЬ копий — зелёный skip там, где GitLab потерял базу. Здесь
    копия делается тем же писателем, что и в бою, и читается тем же
    загрузчиком: учение обязано иметь хотя бы один случай в каждом клоне."""
    from core.state_integrity import append_state_jsonl, backup_state_file

    store = tmp_path / "data" / "store.jsonl"
    store.parent.mkdir(parents=True, exist_ok=True)
    append_state_jsonl(store, [{"a": 1}, {"b": 2}])

    backup = backup_state_file(store)
    rows = read_state_jsonl(backup)

    assert backup.name.endswith(".bak") and backup != store
    assert [r.get("payload", r) for r in rows] == [{"a": 1}, {"b": 2}]


def test_the_drill_would_notice_a_broken_backup(tmp_path) -> None:
    """Контроль: зелёное учение обязано что-то значить."""
    broken = tmp_path / "store.jsonl.bak"
    broken.write_bytes(
        b'{"payload": {"a": 1}}' + b"\n"
        + bytes([0xFF, 0xFE]) + "мусор".encode()
        + b"\n"
    )

    rows = read_state_jsonl(broken)

    assert len(rows) == 1, (
        "битый ряд не отсеян — учение прошло бы и на нечитаемой копии"
    )
