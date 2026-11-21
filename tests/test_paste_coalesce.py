"""Tests for paste-safe stdin reading in the REPL.

Regression: pasting a multi-line block into the interactive prompt used to be
chopped into one separate "question" per line (observed: a single pasted spec
became 12 fragmentary runs). The reader now coalesces a back-to-back burst of
lines — which is how a paste is delivered — into ONE message, while leaving a
human's separately-typed lines untouched.
"""
from __future__ import annotations

import io
import sys
import time

import pytest

import cli.repl as repl_module
from cli.repl import _coalesce_burst, _StdinLineReader
from tests.conftest import call_without_blocking

# ---------- pure burst-coalescing policy ----------


def test_coalesce_joins_a_multiline_burst() -> None:
    pending = ["line two", "line three", None]
    it = iter(pending)
    msg = _coalesce_burst(lambda: "line one", lambda: next(it))
    assert msg == "line one\nline two\nline three"


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
    # `read_message` has no timeout of its own — see `call_without_blocking`.
    msg = call_without_blocking(reader.read_message, "> ")
    assert msg == "spec title\n* bullet a\n* bullet b"


# ---------- a failed read is not the same as end of input ----------


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
        reader.read_line(timeout=5)

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
        assert call_without_blocking(reader.prompt_line, "> ") == "hello"

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
        call_without_blocking(reader.prompt_line, "> ")


# ---------- the same rule, one level down: is stdin a console? ----------

def test_a_missing_or_broken_stdin_is_not_interactive(monkeypatch) -> None:
    """Every state a real stdin can be in must answer "not a console".

    `pythonw.exe` and a detached service give `sys.stdin is None`; a closed
    stream raises ValueError from `isatty()`; a dead descriptor raises OSError.
    All three are the same answer to the caller: there is no terminal here.
    """
    class _ClosedLike:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    class _DeadDescriptor:
        def isatty(self):
            raise OSError("bad file descriptor")

    for stream in (None, _ClosedLike(), _DeadDescriptor()):
        monkeypatch.setattr(sys, "stdin", stream)
        assert repl_module._stdin_is_interactive() is False


def test_a_bug_in_this_module_is_not_reported_as_a_missing_console(monkeypatch) -> None:
    """The narrowing that `_write_prompt` already had (commit 64a31af).

    A broad `except Exception` here answered "not interactive" to everything,
    including a typo in this module: the REPL would quietly switch to
    line-by-line mode and the defect would never be seen. Anything that is not
    a broken or absent stdin belongs to the caller.
    """
    class _Buggy:
        def isatty(self):
            raise AttributeError("a typo in this module, not a broken console")

    monkeypatch.setattr(sys, "stdin", _Buggy())
    with pytest.raises(AttributeError):
        repl_module._stdin_is_interactive()
