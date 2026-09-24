"""patch_check называет блок без строки «FILE: <путь>» (план субботы в, хвост; 24.09).

24.09 писатель положил блоки без строки FILE:, а отказ «text outside blocks»
молчал, почему. (Второй случай плана — старый текст в LINES через «=======» —
проверен: patch_check уже берёт только часть после разделителя; не дефект.)
"""
from __future__ import annotations

from tools.patch_check import marker_hint


def test_a_block_without_file_line_is_named() -> None:
    hint = marker_hint("<<<<<<< SEARCH\nx = 1\n=======\nx = 3\n>>>>>>> REPLACE")
    assert "FILE:" in hint
