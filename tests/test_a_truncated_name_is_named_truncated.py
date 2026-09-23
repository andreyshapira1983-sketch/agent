"""Урезанное имя названо урезанным, а не просто «не найдено».

Замер 2026-09-23, живой прогон. Агент искал `logs/trace_8bdd8f1f06036d05e` и
получил от `find_in_files` ровно «Not found: …» — без единой подсказки. Шаг
потерян, причина не названа, и в тот же ход он не восстановился.

Полное имя следа — `trace_8bdd8f1f06036d05e7746397e8703202.jsonl`. В журналах
агента оно лежит в ЧЕТЫРЁХ разных длинах: 18 упоминаний урезаны до 19 знаков
после `trace_`, 9 — до 25, 191 — целиком без расширения, 1 — целиком с
расширением. Урезанный шестнадцатеричный хвост от целого на вид не
отличается, поэтому агент берёт обрывок и упирается в «файла нет».

Перечислить содержимое папки здесь мало: в `logs/` лежит 370 следов, и первые
восемь по алфавиту не подсказывают ничего. Решает поиск ПРОДОЛЖЕНИЯ имени.

И это была асимметрия между двумя инструментами одного рода: `file_read` уже
подсказывал содержимое ближайшей папки (`_nearest_dir_hint`), а
`find_in_files` не говорил ничего. Агент попал на второй.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.base import truncated_name_hint
from tools.file_read import FileReadTool
from tools.find_in_files import FindInFilesTool


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "trace_8bdd8f1f06036d05e7746397e8703202.jsonl").write_text(
        '{"event": "x"}\n', encoding="utf-8",
    )
    (tmp_path / "logs" / "trace_ffffffffffffffffffffffffffffffff.jsonl").write_text(
        '{"event": "y"}\n', encoding="utf-8",
    )
    return tmp_path


def test_find_in_files_names_the_full_name(workspace: Path) -> None:
    """Тот самый живой случай: обрывок имени следа."""
    tool = FindInFilesTool(workspace_root=workspace)
    with pytest.raises(FileNotFoundError) as excinfo:
        tool.run(query="x", path="logs/trace_8bdd8f1f06036d05e", name="*.jsonl")
    message = str(excinfo.value)
    assert "УРЕЗАННОЕ" in message
    assert "trace_8bdd8f1f06036d05e7746397e8703202.jsonl" in message


def test_file_read_names_the_full_name(workspace: Path) -> None:
    tool = FileReadTool(workspace_root=workspace)
    with pytest.raises(FileNotFoundError) as excinfo:
        tool.run(path="logs/trace_8bdd8f1f06036d05e")
    assert "trace_8bdd8f1f06036d05e7746397e8703202.jsonl" in str(excinfo.value)


def test_a_name_with_no_continuation_still_gets_the_folder_listing(workspace: Path) -> None:
    """Когда продолжения нет, список папки остаётся — он и был раньше.

    Правка ДОБАВЛЯЕТ подсказку, а не отменяет прежнюю: второй живой промах
    того же часа — `core/campaign_goal.py`, файл из ещё не одобренной заявки
    на раскол. Продолжений у этого имени нет, и список папки — единственное,
    что можно сказать.
    """
    (workspace / "core").mkdir()
    (workspace / "core" / "campaign.py").write_text("x = 1\n", encoding="utf-8")
    tool = FileReadTool(workspace_root=workspace)
    with pytest.raises(FileNotFoundError) as excinfo:
        tool.run(path="core/campaign_goal.py")
    message = str(excinfo.value)
    assert "УРЕЗАННОЕ" not in message
    assert "campaign.py" in message


def test_the_hint_does_not_leave_the_workspace(tmp_path: Path) -> None:
    """Подсказка не рассказывает о том, что вне рабочей папки."""
    assert truncated_name_hint(tmp_path, tmp_path.parent / "secrets") == ""


def test_the_hint_survives_an_unreadable_folder(tmp_path: Path) -> None:
    """Сломанная подсказка не должна ронять инструмент: нет подсказки — пусто."""
    assert truncated_name_hint(tmp_path, tmp_path / "нет" / "такой" / "папки.jsonl") == ""


def test_several_continuations_are_all_named(workspace: Path) -> None:
    """Несколько продолжений — называются все, выбор за агентом."""
    hint = truncated_name_hint(workspace, workspace / "logs" / "trace_")
    assert "trace_8bdd8f1f06036d05e7746397e8703202.jsonl" in hint
    assert "trace_ffffffffffffffffffffffffffffffff.jsonl" in hint
