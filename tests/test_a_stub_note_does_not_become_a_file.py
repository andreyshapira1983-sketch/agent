"""Конспект-пустышка не становится файлом.

Задача 5 субботнего списка. Замер 2026-09-23 06:54: цикл помечен `empty` —
система ЗНАЕТ, что работы не было, — а файл
`data/notes/20260923T065402_competence_math.md` записан и лежит рядом с
настоящими. Внутри: «в выводах шагов нет чтения файла», «дословной цитаты
нет», «проверочное вычисление не выполнялось». Со стороны выглядит как
«смотри, я сделал».

Договор конспекта (`core.note_contract`) уже был написан КОДОМ и полон, но
спрашивался только при СУДЕЙСТВЕ цели — файл к тому времени лежал на диске.
Замер того же дня: **40 конспектов из 127 (31%)** содержат такое признание.

Починка — не новый список оборотов, а ОДИН договор, спрошенный в двух местах.
Список заплат закрывает по одной лазейке, а исполнитель находит следующую:
ровно упрёк оператора 2026-09-22 «ты чинишь симптомы».
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.note_contract import contract_gaps
from core.write_at_execution import _refuse_note_without_its_contract

GOOD = (
    "# Конспект\n\n"
    "Источник: core/note_contract.py\n\n"
    "> Здесь критерий задаёт КОД, одинаково для всех учебных задач\n\n"
    "Проверка расчётом: 400 знаков минимума, в файле 63 строки, "
    "проверено сверкой с длиной текста.\n" + ("Пояснение к разбору. " * 30)
)

STUB = (
    "# Конспект\n\n"
    "В выводах шагов этого хода нет чтения файла.\n"
    "Дословной цитаты нет.\n"
    "Проверочное вычисление не выполнялось.\n" + ("Ничего не было сделано. " * 25)
)


class _Loop:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _file_read_workspace_root(self) -> Path:
        return self._root


def test_a_stub_note_fails_its_contract(tmp_path: Path) -> None:
    gaps = contract_gaps(STUB, tmp_path)
    assert gaps, "пустышка обязана проваливать договор"
    # Провал НАЗЫВАЕТ, чего нет: провал без причины агент разгадывает сам
    # и разгадывает неверно.
    joined = " ".join(gaps)
    assert "цитат" in joined and "источник" in joined


def test_a_real_note_passes_its_contract(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "note_contract.py").write_text("x", encoding="utf-8")
    assert contract_gaps(GOOD, tmp_path) == []


def test_a_stub_note_is_refused_BEFORE_it_becomes_a_file(tmp_path: Path) -> None:
    """Главное свойство: отказ до записи, а не вердикт после."""
    with pytest.raises(RuntimeError) as excinfo:
        _refuse_note_without_its_contract(
            _Loop(tmp_path), "data/notes/20260923T065402_competence_math.md", STUB,
        )
    assert "договор" in str(excinfo.value)


def test_a_real_note_is_not_refused(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "note_contract.py").write_text("x", encoding="utf-8")
    _refuse_note_without_its_contract(
        _Loop(tmp_path), "data/notes/good.md", GOOD,
    )


def test_a_file_outside_the_notes_folder_is_not_judged_as_a_note(tmp_path: Path) -> None:
    """Договор конспекта судит конспекты, а не любую запись.

    Иначе обычная правка кода или журнал начали бы требовать дословную цитату
    и проверку с числом — запрет стал бы шире своего основания.
    """
    _refuse_note_without_its_contract(_Loop(tmp_path), "core/llm.py", STUB)
    _refuse_note_without_its_contract(_Loop(tmp_path), "data/report.md", STUB)


def test_an_unknown_workspace_does_not_lock_the_write(tmp_path: Path) -> None:
    """Недоступный корень рабочей папки не запирает запись.

    Проверить договор без корня нельзя: существование названного источника
    проверяется по диску. Молча запретить запись значило бы наказать за
    собственную неспособность проверить.
    """
    class _NoRoot:
        def _file_read_workspace_root(self):
            return None

    _refuse_note_without_its_contract(_NoRoot(), "data/notes/x.md", STUB)
