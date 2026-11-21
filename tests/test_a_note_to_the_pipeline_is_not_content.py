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
