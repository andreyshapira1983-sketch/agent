"""`number_lines` is built and deliberately NOT wired. Here is why.

Background: docs/CODE_NOTES.md, "Numbers cost what memory was already starved of".
"""
from __future__ import annotations

from core.answer_format import number_lines

_FILE = "первая строка\nвторая строка\nтретья строка\n"


def test_the_helper_numbers_from_one():
    """Measured live 2026-08-15: asked for a line, the agent answered 164 and
    382 where the truth was 385 and 450. `file_read` returns bare text, so any
    number it gives is counted by eye over a string it cannot index.
    """
    numbered = number_lines(_FILE)
    assert numbered.splitlines()[0].startswith(" 1\t")
    assert numbered.splitlines()[2].startswith(" 3\t")


def test_an_excerpt_keeps_the_addresses_of_the_whole():
    """The hard half: a line must carry its number in the FILE, not its
    position in the excerpt, or it lies exactly where precision was the point.
    """
    numbered = number_lines("два\nчетыре", original="один\nдва\nтри\nчетыре")
    assert numbered == " 2\tдва\n 4\tчетыре"
