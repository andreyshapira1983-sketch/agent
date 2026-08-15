"""Записка самому конвейеру — не содержимое файла.

Background: docs/CODE_NOTES.md, "A note to the pipeline is not file content".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.placeholder_text import looks_like_unfilled_content

#: Дословно из автономного прогона 2026-08-15. Целью был `core/loop.py` —
#: собственный управляющий цикл агента.
_MEASURED = (
    "TODO: executor заполняет после diff_file; вставляет исправленный код "
    "с конкретным багфикс-изменением и без затрагивания не связанных мест."
)

#: Законные однострочные из ТОГО ЖЕ корпуса — 31 живой вызов `file_write`,
#: 7 из них однострочных. Страж, который тронет их, хуже отсутствующего.
_REAL = [
    "verified-result",
    "Correction: The variable responsible for passing past failures is X",
    "Добавление experience_block в файл.",
]


def test_the_measured_note_is_refused():
    assert looks_like_unfilled_content(_MEASURED) is True


@pytest.mark.parametrize("content", _REAL)
def test_real_one_line_content_still_passes(content: str):
    assert looks_like_unfilled_content(content) is False


@pytest.mark.parametrize("content", [
    "FIXME — заполнить позже",
    "XXX: сюда пойдёт настоящий разбор",
    "HACK: временная заглушка, переписать",
    "todo: fill in the real body",
])
def test_unseen_markers_of_the_same_class_are_refused(content: str):
    """Формы, под которые правило не подгоняли: другие маркеры, другой регистр,
    другой язык. Класс один — файл, целиком состоящий из записки о том, что в
    нём будет.
    """
    assert looks_like_unfilled_content(content) is True


def test_a_todo_comment_at_the_top_of_a_real_file_is_untouched():
    """Граница правила и его единственная защита от жадности: маркер судится
    только у ОДНОСТРОЧНОГО содержимого. `# TODO: …` первой строкой настоящего
    файла — законный комментарий, и файл за него наказывать нельзя.
    """
    assert looks_like_unfilled_content(
        "# TODO: refactor this later\nimport os\n\n\ndef main():\n    return 1\n"
    ) is False


def test_the_angle_bracket_form_still_works():
    """Улов не отдан: прежняя форма ловится по-прежнему."""
    assert looks_like_unfilled_content(
        "<updated content for core/loop.py with experience_block integration>"
    ) is True


def test_the_tool_itself_refuses_the_note(tmp_path: Path):
    """Живой путь, а не только предикат.

    В прогоне 2026-08-15 запись остановил не этот страж, а случайно висевшая
    заявка на одобрение (`readiness_blocker`). Полагаться на случайность нельзя
    — тест держит сам инструмент.
    """
    from tools.file_write import FileWriteTool

    tool = FileWriteTool(tmp_path)

    with pytest.raises(ValueError, match="unfilled placeholder"):
        tool.run(path="core/loop.py", content=_MEASURED)


def test_the_tool_still_writes_real_content(tmp_path: Path):
    """Ломка наоборот: отсекая записку, не отсечь работу."""
    from tools.file_write import FileWriteTool

    tool = FileWriteTool(tmp_path)
    tool.run(path="notes.md", content="Correction: the variable is X")

    assert (tmp_path / "notes.md").read_text(encoding="utf-8").startswith("Correction")
