"""`number_lines` is built and deliberately NOT wired. Here is why.

Background: docs/CODE_NOTES.md, "Numbers cost what memory was already starved of".
"""
from __future__ import annotations

from core.answer_format import format_artifact, number_lines

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


def test_it_is_not_wired_into_the_prompt():
    """Deliberate, and this test is the record of the decision.

    The gutter costs ~8.6% on a real file (measured on core/loop.py). Long-term
    memory is spent FIRST by design (MIR-092) and already reaches the model as
    nothing, so those characters come out of the agent's own recollection.
    Wiring this would buy an address column with the thing MIR-096 says is
    already broken. Fix the starvation first; then the numbers are affordable.

    When that changes, delete this test and pin the rendering instead.
    """
    assert format_artifact("file_read", _FILE, question="x") == _FILE
