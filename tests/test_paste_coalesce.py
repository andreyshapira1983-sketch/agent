"""Tests for paste-safe stdin reading in the REPL.

Regression: pasting a multi-line block into the interactive prompt used to be
chopped into one separate "question" per line (observed: a single pasted spec
became 12 fragmentary runs). The reader now coalesces a back-to-back burst of
lines — which is how a paste is delivered — into ONE message, while leaving a
human's separately-typed lines untouched.
"""
from __future__ import annotations

import io
import queue
import time

import pytest

from main import _coalesce_burst, _StdinLineReader


# ---------- pure burst-coalescing policy ----------

def test_coalesce_single_line_is_unchanged() -> None:
    # read_next immediately reports "burst over" -> just the first line.
    msg = _coalesce_burst(lambda: "hello world", lambda: None)
    assert msg == "hello world"


def test_coalesce_joins_a_multiline_burst() -> None:
    pending = ["line two", "line three", None]
    it = iter(pending)
    msg = _coalesce_burst(lambda: "line one", lambda: next(it))
    assert msg == "line one\nline two\nline three"


def test_coalesce_stops_at_first_gap() -> None:
    # None ends the burst even if more would come later.
    it = iter(["b", None, "should-not-be-read"])
    msg = _coalesce_burst(lambda: "a", lambda: next(it))
    assert msg == "a\nb"


def test_coalesce_propagates_eof_from_first_read() -> None:
    def _boom() -> str:
        raise EOFError

    with pytest.raises(EOFError):
        _coalesce_burst(_boom, lambda: None)


# ---------- threaded reader ----------

def _reader_over(lines: list[str], *, interactive: bool) -> _StdinLineReader:
    """Build a reader whose backing 'stdin' yields `lines` then EOF ("")."""
    src = iter([*lines, ""])
    return _StdinLineReader(
        interactive=interactive,
        readline=lambda: next(src),
        out=io.StringIO(),
        gap_seconds=0.05,
    )


def test_reader_coalesces_paste_when_interactive() -> None:
    reader = _reader_over(["spec title", "* bullet a", "* bullet b"], interactive=True)
    # All three lines are available near-instantly (like a paste), so the drain
    # collects them into one message.
    time.sleep(0.02)  # let the pump enqueue
    msg = reader.read_message("> ")
    assert msg == "spec title\n* bullet a\n* bullet b"


def test_reader_reads_one_line_at_a_time_when_not_interactive() -> None:
    reader = _reader_over(["cmd one", "cmd two"], interactive=False)
    # Non-interactive (piped/scripted) input must keep line-by-line semantics.
    assert reader.read_message("> ") == "cmd one"
    assert reader.read_message("> ") == "cmd two"


def test_reader_prompt_line_is_blocking_single_line() -> None:
    reader = _reader_over(["only line"], interactive=True)
    assert reader.prompt_line("... ") == "only line"


def test_reader_raises_eof_at_end_of_input() -> None:
    reader = _reader_over([], interactive=True)
    with pytest.raises(EOFError):
        reader.read_line(timeout=1.0)


def test_reader_read_line_times_out_when_nothing_available() -> None:
    # A reader whose source blocks forever: read_line(timeout) must raise Empty.
    ev = queue.Queue()  # never fed

    reader = _StdinLineReader(
        interactive=True,
        readline=ev.get,  # blocks until something is put (never)
        out=io.StringIO(),
        gap_seconds=0.05,
    )
    with pytest.raises(queue.Empty):
        reader.read_line(timeout=0.05)
