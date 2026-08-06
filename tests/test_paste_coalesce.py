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
import sys
import time

import pytest

from cli.repl import _coalesce_burst, _StdinLineReader

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


# ---------- a failed read is not the same as end of input ----------

def test_end_of_input_leaves_no_error_and_says_nothing(capsys):
    """The honest case: stdin ended, nothing went wrong."""
    reader = _StdinLineReader(interactive=False, readline=lambda: "", out=io.StringIO())

    with pytest.raises(EOFError):
        reader.read_line()

    assert reader.read_error is None
    assert capsys.readouterr().err == ""


def test_a_failed_read_records_the_cause_and_reports_it(capsys):
    """Both a broken read and a real EOF stop the reader and surface as
    EOFError. Before this, that was the whole story: a decode error, a closed
    pipe and an honest Ctrl+D ended the session identically, with nothing
    written anywhere.
    """
    def _broken() -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    reader = _StdinLineReader(interactive=False, readline=_broken, out=io.StringIO())

    with pytest.raises(EOFError):
        reader.read_line()

    assert isinstance(reader.read_error, UnicodeDecodeError)
    err = capsys.readouterr().err
    assert "stdin read failed" in err
    assert "UnicodeDecodeError" in err


def test_a_failure_while_reporting_still_delivers_the_eof():
    """The notice is best effort; ending the session is not.

    A reader thread that raised while printing would leave the queue without
    its EOF marker and hang the REPL on the next read.
    """
    class _AngryStderr:
        def write(self, *a, **k):
            raise OSError("stderr is gone")

        def flush(self, *a, **k):
            raise OSError("stderr is gone")

        def isatty(self):
            return False

    def _broken() -> str:
        raise OSError("pipe closed")

    reader = _StdinLineReader(interactive=False, readline=_broken, out=io.StringIO())
    real_stderr, sys.stderr = sys.stderr, _AngryStderr()
    try:
        with pytest.raises(EOFError):
            reader.read_line(timeout=5)
    finally:
        sys.stderr = real_stderr

    assert isinstance(reader.read_error, OSError)


def test_the_reader_keeps_signalling_eof_after_a_failure():
    """Every later read must also raise, not block."""
    reader = _StdinLineReader(
        interactive=False,
        readline=lambda: (_ for _ in ()).throw(OSError("gone")),
        out=io.StringIO(),
    )

    for _ in range(3):
        with pytest.raises(EOFError):
            reader.read_line(timeout=5)


def test_prompt_failures_are_swallowed_but_bugs_are_not():
    """`_write_prompt` catches only what a broken console raises.

    A closed stream and an unencodable prompt raise ValueError
    (UnicodeEncodeError is one); a dead pipe raises OSError. Anything else —
    an AttributeError from a typo, for instance — is a defect here and must
    reach the caller instead of hiding behind a lost prompt.
    """
    class _DeadPipe:
        def write(self, s):
            raise OSError("pipe closed")

        def flush(self):
            raise OSError("pipe closed")

    class _ClosedStream:
        def write(self, s):
            raise ValueError("I/O operation on closed file")

        def flush(self):
            pass

    for out in (_DeadPipe(), _ClosedStream()):
        # Finite: one line then EOF. An endless lambda leaves the pump
        # thread filling the queue forever, which turns a failing run
        # into a hanging one.
        src = iter(["hello" + chr(10), ""])
        reader = _StdinLineReader(interactive=False, readline=lambda _s=src: next(_s), out=out)
        assert reader.prompt_line("> ") == "hello"

    class _Buggy:
        def write(self, s):
            raise AttributeError("a typo in this module, not a broken console")

        def flush(self):
            pass

    # Finite: one line then EOF. An endless lambda leaves the pump
    # thread filling the queue forever, which turns a failing run
    # into a hanging one.
    src = iter(["hello" + chr(10), ""])
    reader = _StdinLineReader(interactive=False, readline=lambda _s=src: next(_s), out=_Buggy())
    with pytest.raises(AttributeError):
        reader.prompt_line("> ")
