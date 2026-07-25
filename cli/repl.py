"""Interactive REPL input: one owner for stdin, paste-safe.

The REPL reads with line-buffered input, so a pasted multi-line block used to
arrive as many separate prompts -- each executed as its own question. The fix
does not depend on terminal features (Windows cooked-mode input does not surface
bracketed-paste markers): a single background thread drains stdin into a queue,
and a top-level read *coalesces* the burst of lines a paste delivers back-to-back
into ONE message. A human typing pauses between lines, so their lines are not
merged.

Making :class:`_StdinLineReader` the ONLY consumer of stdin is what keeps the
top-level prompt, the block modes and the approval prompt from racing each other
-- they all pull from the same queue.

Extracted verbatim from ``main.py``; it re-exports every name here, so
``from main import _StdinLineReader`` and ``main.PASTE_COALESCE_GAP_SECONDS``
keep working for the test modules that use them. The REPL loop itself still
lives in ``main()`` and will join this module in a later step.
"""
from __future__ import annotations

import queue
import sys
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation only -- it was an unresolved string in main.py
    from io import TextIOBase


def _collect_instruction_buffer(
    read_line: Callable[[], str],
) -> tuple[str, bool]:
    """Collect operator instruction lines until a terminator marker.

    Reads lines via ``read_line`` until ``:task-end`` (commit) or
    ``:task-abort`` (discard). Returns ``(text, cancelled)`` where ``text`` is
    the joined+stripped buffer and ``cancelled`` is ``True`` when the operator
    aborted. ``read_line`` may raise ``EOFError``/``KeyboardInterrupt``; that is
    propagated so the caller can treat it as a request to leave the REPL.
    """
    lines: list[str] = []
    while True:
        line = read_line()
        marker = line.strip().lower()
        if marker == ":task-end":
            return "\n".join(lines).strip(), False
        if marker == ":task-abort":
            return "", True
        lines.append(line)


# ── Paste-safe stdin reading ──────────────────────────────────────────────
# The REPL reads with line-buffered input, so pasting a multi-line block used
# to arrive as many separate prompts — each executed as its own question
# (observed: one pasted spec became 12 fragmentary "questions"). We fix this
# without depending on terminal features (Windows cooked-mode input does not
# surface bracketed-paste markers): a single background thread drains stdin
# into a queue, and a top-level read "coalesces" the burst of lines that a
# paste delivers back-to-back into ONE message. A human typing pauses between
# lines, so their lines are NOT merged.

# Max wait for the *next* line before deciding a burst has ended. A paste
# delivers its lines within microseconds; a human takes far longer. Small
# enough to never merge separate human submissions, large enough to catch a
# paste even on a slightly laggy terminal.
PASTE_COALESCE_GAP_SECONDS = 0.05


def _coalesce_burst(
    read_first: Callable[[], str],
    read_next: Callable[[], str | None],
) -> str:
    """Join a back-to-back burst of input lines into one message.

    ``read_first`` blocks for the first line (and may raise
    ``EOFError``/``KeyboardInterrupt``, which propagate). ``read_next``
    returns the next line if one is already waiting, or ``None`` when the
    burst has ended (nothing arrived within the grace window). Lines are
    joined with ``\\n`` so a pasted block keeps its structure.
    """
    parts = [read_first()]
    while True:
        nxt = read_next()
        if nxt is None:
            break
        parts.append(nxt)
    return "\n".join(parts)


class _StdinLineReader:
    """Single-owner, thread-backed line reader for the interactive REPL.

    A daemon thread performs the blocking reads so the main thread can pull
    lines with a timeout (needed for paste coalescing). Making this the ONLY
    consumer of stdin avoids races between the top-level prompt, the block
    modes, and the approval prompt — they all pull from the same queue.
    """

    _EOF = object()

    def __init__(
        self,
        *,
        interactive: bool,
        readline: Callable[[], str] | None = None,
        out: "TextIOBase | None" = None,
        gap_seconds: float = PASTE_COALESCE_GAP_SECONDS,
    ) -> None:
        self._readline = readline or sys.stdin.readline
        self._interactive = interactive
        self._out = out or sys.stdout
        self._gap = gap_seconds
        self._q: "queue.Queue[object]" = queue.Queue()
        self._started = False
        self._lock = threading.Lock()

    def _ensure_started(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        while True:
            try:
                line = self._readline()
            except Exception:
                self._q.put(self._EOF)
                return
            if line == "":  # EOF (Ctrl+Z / Ctrl+D / closed pipe)
                self._q.put(self._EOF)
                return
            self._q.put(line.rstrip("\n").rstrip("\r"))

    def read_line(self, timeout: float | None = None) -> str:
        """Return the next line. Raises EOFError at end of input, or
        ``queue.Empty`` when ``timeout`` elapses with nothing available."""
        self._ensure_started()
        item = self._q.get(timeout=timeout)  # may raise queue.Empty
        if item is self._EOF:
            self._q.put(self._EOF)  # keep signalling EOF to later reads
            raise EOFError
        return item  # type: ignore[return-value]

    def _write_prompt(self, prompt: str) -> None:
        try:
            self._out.write(prompt)
            self._out.flush()
        except Exception:
            pass

    def prompt_line(self, prompt: str) -> str:
        """Blocking single-line read with a visible prompt (block modes)."""
        self._write_prompt(prompt)
        return self.read_line()

    def read_message(self, prompt: str) -> str:
        """Read one logical message, coalescing a pasted multi-line burst.

        Non-interactive input (pipes, tests) is read strictly one line at a
        time so scripted command streams keep their original semantics.
        """
        self._write_prompt(prompt)
        if not self._interactive:
            return self.read_line()

        def _next() -> str | None:
            try:
                return self.read_line(timeout=self._gap)
            except queue.Empty:
                return None
            except EOFError:
                return None

        return _coalesce_burst(self.read_line, _next)


def _stdin_is_interactive() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False
