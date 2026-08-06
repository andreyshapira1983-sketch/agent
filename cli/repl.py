"""Interactive REPL input: one owner for stdin, and the dialogue loop.

:class:`_StdinLineReader` is the ONLY consumer of stdin — the top-level prompt,
the block modes and the approval prompt all pull from its queue, so they cannot
race. :func:`run_repl` is the loop: input modes, ``:command`` dispatch, the
intent router, the rate-limit check, the agent call. Startup wiring lives in
``cli/app.py``, which calls this.

Design notes and the measurements behind them: docs/CODE_NOTES.md.
"""
from __future__ import annotations

import queue
import sys
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from app import budget_guard, operator_task
from cli import command_dispatch, intent_bridge
from core.loop import format_human_response

if TYPE_CHECKING:  # annotation only -- it was an unresolved string in main.py
    from io import TextIOBase
    from pathlib import Path

    from core.rate_limiter import CLIRateLimiter


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


def _collect_pasted_block(read_line: Callable[[], str]) -> str:
    """Collect a ``<<< … >>>`` block, joined with newlines and stripped.

    Ends on the first line ending in ``>>>``, whether alone or glued to a paste
    ("...text>>>"), keeping what precedes it. May return ""; what that means is
    the caller's call. ``EOFError``/``KeyboardInterrupt`` propagate.
    """
    parts: list[str] = []
    while True:
        line = read_line()
        stripped = line.strip()
        if stripped.endswith(">>>"):
            parts.append(line.rstrip()[:-3].rstrip())
            break
        parts.append(line)
    return "\n".join(parts).strip()


#: Max wait for the NEXT line before a burst counts as over. A paste delivers
#: its lines in microseconds, a human takes far longer — that gap is the whole
#: mechanism, so this value is a contract, not a tuning knob.
PASTE_COALESCE_GAP_SECONDS = 0.05


def _coalesce_burst(
    read_first: Callable[[], str],
    read_next: Callable[[], str | None],
) -> str:
    """Join a back-to-back burst of input lines into one message.

    ``read_first`` blocks and may raise ``EOFError``/``KeyboardInterrupt``, which
    propagate; ``read_next`` returns ``None`` once the burst is over.
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

    A daemon thread does the blocking reads so the main thread can pull lines
    with a timeout, which is what makes paste coalescing possible.
    """

    _EOF = object()

    def __init__(
        self,
        *,
        interactive: bool,
        readline: Callable[[], str] | None = None,
        out: TextIOBase | None = None,
        gap_seconds: float = PASTE_COALESCE_GAP_SECONDS,
    ) -> None:
        self._readline = readline or sys.stdin.readline
        self._interactive = interactive
        self._out = out or sys.stdout
        self._gap = gap_seconds
        self._q: queue.Queue[object] = queue.Queue()
        self._started = False
        self._lock = threading.Lock()
        #: What ended the pump, or None if input simply ran out. Both surface as
        #: EOFError to the caller; this is what tells them apart.
        self.read_error: BaseException | None = None

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
            except Exception as exc:  # noqa: BLE001 — a reader thread may not die loudly
                # A thread that cannot read stdin has nothing left to do, so it
                # ends here; the reason survives in `read_error`.
                self.read_error = exc
                self._report_read_failure(exc)
                self._q.put(self._EOF)
                return
            if line == "":  # EOF (Ctrl+Z / Ctrl+D / closed pipe)
                self._q.put(self._EOF)
                return
            self._q.put(line.rstrip("\n").rstrip("\r"))

    def _report_read_failure(self, exc: BaseException) -> None:
        """Say once, on stderr, that input ended by failure and not by EOF.

        Guarded: a reader thread that raises while reporting would leave the
        queue without its EOF and hang the session.
        """
        try:
            print(
                f"(stdin read failed: {type(exc).__name__}: {exc}; "
                "ending the session)",
                file=sys.stderr,
                flush=True,
            )
        except Exception:  # noqa: BLE001, S110 — the EOF below matters more
            pass  # nosec B110 — see docstring: the queue must still get its EOF

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
        except (OSError, ValueError):
            # A broken stdout must not stop input from being read. Narrow on
            # purpose: those two are a broken console, anything else is a bug
            # here and belongs to the caller.
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


def _ask_the_agent(
    agent: object,
    question: str,
    *,
    rate_limiter: CLIRateLimiter,
    workspace: Path,
    file_hint: str | None,
) -> None:
    """Spend one rate-limit token, run the agent, print the answer.

    Returns nothing: a refused token and a delivered answer both mean "this
    message is done". `budget_guard` is addressed through the MODULE because the
    suites patch it there, and a name bound at import time would not see that.
    """
    rl = rate_limiter.consume()
    if not rl.allowed:
        print(
            f"(rate limit: too many requests — "
            f"retry in {rl.retry_after_seconds:.1f}s, "
            f"tokens remaining: {rl.tokens_remaining:.2f})",
            file=sys.stderr,
        )
        return
    answer = budget_guard._run_agent_with_budget_guard(
        agent,
        user_question=question,
        file_hint=file_hint,
        workspace=workspace,
        stream=False,
    )
    print("\n" + format_human_response(answer) + "\n")


def _stdin_is_interactive() -> bool:
    """True when stdin is a terminal — the condition for paste coalescing.

    No stdin at all (``pythonw.exe``), a closed stream and a dead descriptor are
    one answer: no console. Anything else is a bug here and must reach the
    caller, not be answered with a quiet "not interactive".
    """
    stream = sys.stdin
    if stream is None:
        return False
    try:
        return bool(stream.isatty())
    except (OSError, ValueError):
        return False


# The order of the checks in the loop below is pinned by the characterization
# suites; the collaborators are called through their MODULE so one
# monkeypatch is seen from here and from cli/one_shot.py alike.


def run_repl(
    agent: object,
    *,
    reader: _StdinLineReader,
    rate_limiter: CLIRateLimiter,
    workspace: Path,
    file_hint: str | None = None,
) -> int:
    """Run the interactive dialogue until EOF/Ctrl+C and return the exit code.

    Returns ``0`` on every way out: end of input, Ctrl+C, or an abandoned block
    mode. ``:quit``/``:exit`` leave through ``SystemExit`` raised by the
    dispatcher, which passes straight through this loop.
    """
    while True:
        try:
            q = reader.read_message("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            # An empty Enter must NOT exit: a paste whose first line is blank
            # would drop the operator into the parent shell, which then runs the
            # rest of the paste as commands. Leave with :quit / Ctrl+C / EOF.
            continue
        # Mode 1: an explicit block, <<< … >>>, for pasting text with newlines.
        if q == "<<<":
            print("(multi-line mode: paste text, finish with >>> on its own line)",
                  file=sys.stderr)
            try:
                q = _collect_pasted_block(lambda: reader.prompt_line("... "))
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not q:
                continue
        # Mode 2: a line ending in \ is joined with the next, backslash removed.
        elif q.endswith("\\"):
            continuation_parts: list[str] = [q[:-1]]
            while True:
                try:
                    cline = reader.prompt_line("... ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
                if cline.endswith("\\"):
                    continuation_parts.append(cline[:-1])
                else:
                    continuation_parts.append(cline)
                    break
            q = " ".join(p.strip() for p in continuation_parts if p.strip())
            if not q:
                # The same refusal the other two paths make: an empty message
                # costs a rate-limit token and asks the agent nothing.
                continue
        if q == ":operator-task":
            block_lines: list[str] = []
            print("(operator task block started; finish with :end)", file=sys.stderr)
            while True:
                try:
                    line = reader.prompt_line("... ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
                if line.strip().lower() == ":end":
                    break
                block_lines.append(line)
            operator_task._handle_operator_task("\n".join(block_lines), agent, workspace)
            continue
        # :task-begin … :task-end goes STRAIGHT to the agent, bypassing the
        # keyword router — the reliable way to send wording that a shortcut
        # would otherwise hijack (text merely mentioning budget or approval).
        if q == ":task-begin":
            print(
                "(instruction buffer started; finish with :task-end, "
                "discard with :task-abort)",
                file=sys.stderr,
            )
            try:
                buffered, cancelled = _collect_instruction_buffer(
                    lambda: reader.prompt_line("... ")
                )
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if cancelled:
                print("(instruction buffer cancelled)", file=sys.stderr)
                continue
            if not buffered:
                print("(instruction buffer empty — nothing sent)", file=sys.stderr)
                continue
            if intent_bridge._handle_local_operator_reply(buffered, agent):
                continue
            _ask_the_agent(
                agent, buffered,
                rate_limiter=rate_limiter, workspace=workspace, file_hint=file_hint,
            )
            continue
        if q.startswith(":") or q == "?":
            if command_dispatch.handle_meta_command(q, agent, workspace):
                continue
            print(f"(unknown command: {q})", file=sys.stderr)
            continue
        if intent_bridge._handle_local_operator_reply(q, agent):
            continue
        if intent_bridge.handle_conversational_operator_input(q, agent, workspace):
            continue
        # ── Rate-limit check, then the agent ─────────────────────────────────
        _ask_the_agent(
            agent, q,
            rate_limiter=rate_limiter, workspace=workspace, file_hint=file_hint,
        )
