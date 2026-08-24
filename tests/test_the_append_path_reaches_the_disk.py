"""Дописанная строка состояния доходит до диска, а не только до кэша.

СВЕРКА С ПОЛЕМ, класс H-50 (docs/audit/HISTORICAL_FAILURE_LEDGER.md) —
согласованность файловой системы при обрыве: ext3 в режиме `writeback`,
семантика `fsync`. Урок: запись, вернувшая успех, ещё не на диске; при потере
питания хвост файла теряется или оказывается рваным.

ЗАМЕР 2026-08-25. Барьер долговечности стоял на пути АТОМАРНОЙ ПЕРЕЗАПИСИ
(`_atomic_write_lines`) и отсутствовал на пути ДОПИСЫВАНИЯ — при том что
дописывание горячее: через него идут каждый резерв бюджета, каждая строка
расхода и каждое обновление задачи. Потеря хвоста здесь бьёт в ту же сторону,
что H-27: потолок ЗАБЫВАЕТ потраченное, а задача может исполниться дважды.

ЦЕНА ИЗМЕРЕНА, А НЕ ОЦЕНЕНА НА ГЛАЗ: +0,628 мс на строку. На живом объёме
бюджета (3302 строки за месяц) это около 2 секунд суммарно — против тика,
который тратит секунды на один вызов модели.

Тест смотрит на сам СИСТЕМНЫЙ ВЫЗОВ, а не на текст функции: проба, которая
ищет слово `fsync` в исходнике, меряет адрес, а не свойство.
"""
from __future__ import annotations

import os

import pytest

from core.state_integrity import append_state_jsonl, read_state_jsonl


def test_appending_asks_the_filesystem_for_durability(tmp_path, monkeypatch) -> None:
    seen: list[int] = []
    real_fsync = os.fsync

    def _spy(fd: int) -> None:
        seen.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", _spy)
    append_state_jsonl(tmp_path / "s.jsonl", [{"kind": "важное", "amount": 1}])

    assert seen, (
        "дописывание вернуло успех, ни разу не попросив файловую систему "
        "донести данные до диска — при обрыве питания хвост теряется"
    )


def test_the_row_is_still_readable_after_the_barrier(tmp_path) -> None:
    """Контроль: барьер не должен ломать то, ради чего запись делалась."""
    path = tmp_path / "s.jsonl"
    append_state_jsonl(path, [{"kind": "первая", "n": 1}])
    append_state_jsonl(path, [{"kind": "вторая", "n": 2}])

    rows = read_state_jsonl(path)
    assert [r["kind"] for r in rows] == ["первая", "вторая"]


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


@pytest.mark.parametrize("rows", [1, 5])
def test_one_barrier_per_call_not_per_row(tmp_path, monkeypatch, rows: int) -> None:
    """Цена держится: барьер ставится на вызов, а не на каждую строку.

    Иначе пакетная запись платила бы за долговечность столько раз, сколько в
    ней строк, и измеренная цена перестала бы быть верной.
    """
    seen: list[int] = []
    real_fsync = os.fsync

    def _spy(fd: int) -> None:
        seen.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", _spy)

    append_state_jsonl(
        tmp_path / "s.jsonl", [{"kind": f"строка {i}", "n": i} for i in range(rows)]
    )

    assert len(seen) == 1, f"барьеров {len(seen)} на {rows} строк"
