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
keep working for the test modules that use them.

:func:`run_repl` at the bottom of this file is the dialogue loop itself, moved
here from ``main()`` in a later step. It owns everything that happens *per
message*: the two multi-line input modes (``<<< … >>>`` and trailing ``\\``),
the ``:operator-task``/``:end`` and ``:task-begin``/``:task-end`` blocks,
``:command`` dispatch, the plain-language intent router, the rate-limit check
and the agent call. What stays in ``main()`` is the one-time *wiring* around it
(reader, approval provider, agent, rate limiter, daemon notice, banner), because
that is startup ordering and several suites freeze it by patching ``main``.
"""
from __future__ import annotations

import queue
import sys
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.budget_guard import _run_agent_with_budget_guard
from app.operator_task import _handle_operator_task
from cli.command_dispatch import handle_meta_command as _handle_meta_command
from cli.intent_bridge import (
    _handle_local_operator_reply,
    handle_conversational_operator_input as _handle_conversational_operator_input,
)
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


# ── The dialogue loop ─────────────────────────────────────────────────────────
# Moved out of ``main()``; the body below is the original ``while True:`` block
# dedented one level, with the locals it used renamed to parameters
# (``_reader`` -> ``reader``, ``_rate_limiter`` -> ``rate_limiter``,
# ``args.file`` -> ``file_hint``). Every printed string, every branch and the
# order of the checks are untouched, and that order is what
# ``tests/characterization/test_repl_input_modes.py``,
# ``test_cli_command_precedence.py`` and ``test_repl_rate_limit_paths.py`` pin.
#
# The five collaborators are keyword parameters for the same reason as in
# ``cli/one_shot.py``: ``main`` is a monkeypatch surface
# (``docs/refactor/CLI_BASELINE.md`` section 2.5), and a patch on ``main`` is
# only observed where the call site resolves the name in ``main``'s namespace.
# ``main()`` passes its own bindings in; the defaults here are for direct
# callers. Both seams come out in Phase 7 with the re-export block.


def run_repl(
    agent: object,
    *,
    reader: _StdinLineReader,
    rate_limiter: CLIRateLimiter,
    workspace: Path,
    file_hint: str | None = None,
    handle_meta_command: Callable[..., bool] = _handle_meta_command,
    handle_local_operator_reply: Callable[..., bool] = _handle_local_operator_reply,
    handle_conversational: Callable[..., bool] = _handle_conversational_operator_input,
    run_agent_with_budget_guard: Callable[..., str] = _run_agent_with_budget_guard,
    handle_operator_task: Callable[..., object] = _handle_operator_task,
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
            # An empty Enter must NOT exit — otherwise pasting a long
            # multi-line block whose first line is blank (or pressing
            # Enter to clear the prompt) drops the user back into the
            # parent shell, which then tries to interpret the rest of
            # the paste as commands. Use :quit / :exit / Ctrl+C / EOF.
            continue
        # ── Multi-line input modes ────────────────────────────────────────────
        # Mode 1: explicit block  <<<  … >>>
        #   Start a line with <<< to enter block mode; finish with >>>
        #   Useful when pasting text that contains newlines.
        if q == "<<<":
            block_parts: list[str] = []
            print("(multi-line mode: paste text, finish with >>> on its own line)",
                  file=sys.stderr)
            while True:
                try:
                    bline = reader.prompt_line("... ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
                stripped = bline.strip()
                if stripped == ">>>":
                    break
                # Tolerate the terminator glued to the end of a paste:
                # "...last sentence.>>>" should also end the block, otherwise
                # users get stuck in `... ` prompt forever after a single
                # Ctrl+V whose buffer ended with ">>>" without a newline.
                if stripped.endswith(">>>"):
                    block_parts.append(bline.rstrip()[:-3].rstrip())
                    break
                block_parts.append(bline)
            q = "\n".join(block_parts).strip()
            if not q:
                continue
        # Mode 2: line continuation with trailing backslash
        #   Each line ending in \ is joined with the next (backslash removed).
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
        # ─────────────────────────────────────────────────────────────────────
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
            handle_operator_task("\n".join(block_lines), agent, workspace)
            continue
        # ── CLI instruction buffer ────────────────────────────────────────────
        # :task-begin … :task-end lets the operator compose a complex,
        # multi-line instruction that is sent straight to the agent, bypassing
        # the operator keyword router. This is the reliable way to give an
        # instruction whose wording would otherwise be hijacked by a shortcut
        # (e.g. text that merely *mentions* budget / approval / implementation).
        # :task-abort discards the buffer.
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
            if handle_local_operator_reply(buffered, agent):
                continue
            rl = rate_limiter.consume()
            if not rl.allowed:
                print(
                    f"(rate limit: too many requests — "
                    f"retry in {rl.retry_after_seconds:.1f}s, "
                    f"tokens remaining: {rl.tokens_remaining:.2f})",
                    file=sys.stderr,
                )
                continue
            answer = run_agent_with_budget_guard(
                agent,
                user_question=buffered,
                file_hint=file_hint,
                workspace=workspace,
                stream=False,
            )
            print("\n" + format_human_response(answer) + "\n")
            continue
        if q.startswith(":") or q == "?":
            if handle_meta_command(q, agent, workspace):
                continue
            print(f"(unknown command: {q})", file=sys.stderr)
            continue
        if handle_local_operator_reply(q, agent):
            continue
        if handle_conversational(q, agent, workspace):
            continue
        # ── Rate-limit check ─────────────────────────────────────────────────
        rl = rate_limiter.consume()
        if not rl.allowed:
            print(
                f"(rate limit: too many requests — "
                f"retry in {rl.retry_after_seconds:.1f}s, "
                f"tokens remaining: {rl.tokens_remaining:.2f})",
                file=sys.stderr,
            )
            continue
        answer = run_agent_with_budget_guard(
            agent,
            user_question=q,
            file_hint=file_hint,
            workspace=workspace,
            stream=False,
        )
        print("\n" + format_human_response(answer) + "\n")
