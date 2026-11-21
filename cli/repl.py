"""Interactive REPL: the single stdin reader and the dialogue loop."""
from __future__ import annotations

import queue
import sys
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from app import budget_guard, operator_task
from cli import command_dispatch, intent_bridge
from core.loop import format_human_response

if TYPE_CHECKING:  # annotation only
    from io import TextIOBase
    from pathlib import Path

    from core.rate_limiter import CLIRateLimiter


def _collect_instruction_buffer(
    read_line: Callable[[], str],
) -> tuple[str, bool]:
    """Collect operator instruction lines until a terminator marker."""
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
    """Collect a ``<<< … >>>`` block, joined with newlines and stripped."""
    parts: list[str] = []
    while True:
        line = read_line()
        stripped = line.strip()
        if stripped.endswith(">>>"):
            parts.append(line.rstrip()[:-3].rstrip())
            break
        parts.append(line)
    return "\n".join(parts).strip()


#: Max wait for the next line of a burst: a paste arrives in microseconds, a human far slower.
#: That gap is the whole mechanism, so the value is a contract, not a tuning knob.
PASTE_COALESCE_GAP_SECONDS = 0.05


def _collect_operator_task_block(read_line: Callable[[], str]) -> str:
    """Collect an ``:operator-task`` block, joined with newlines."""
    lines: list[str] = []
    while True:
        line = read_line()
        if line.strip().lower() == ":end":
            return "\n".join(lines)
        lines.append(line)


def _collect_continuation(first_line: str, read_line: Callable[[], str]) -> str:
    """Join a line ending in ``\\`` with the ones that continue it."""
    parts: list[str] = [first_line[:-1]]
    while True:
        line = read_line()
        if line.endswith("\\"):
            parts.append(line[:-1])
        else:
            parts.append(line)
            break
    return " ".join(part.strip() for part in parts if part.strip())


def _coalesce_burst(
    read_first: Callable[[], str],
    read_next: Callable[[], str | None],
) -> str:
    """Join a back-to-back burst of lines into one message; read_next returns None at its end."""
    parts = [read_first()]
    while True:
        nxt = read_next()
        if nxt is None:
            break
        parts.append(nxt)
    return "\n".join(parts)


class _StdinLineReader:
    """Sole stdin owner: a daemon thread reads, callers pull lines with a timeout.

    The top-level prompt, block modes and approval prompt all read from its queue, so none race.
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
        #: The read failure that ended the pump (None on plain EOF); both reach callers as EOFError.
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
                # Nothing is left to do without stdin; the reason survives in read_error.
                self.read_error = exc
                self._report_read_failure(exc)
                self._q.put(self._EOF)
                return
            if line == "":  # EOF (Ctrl+Z / Ctrl+D / closed pipe)
                self._q.put(self._EOF)
                return
            self._q.put(line.rstrip("\n").rstrip("\r"))

    def _report_read_failure(self, exc: BaseException) -> None:
        """Say once on stderr that input ended by failure, not EOF.

        Guarded: raising here would leave the queue without its EOF and hang the session.
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
        """Return the next line; raise EOFError at end of input, queue.Empty on timeout."""
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
            # A broken console must not stop input; any other error is a bug for the caller.
            pass

    def prompt_line(self, prompt: str) -> str:
        """Blocking single-line read with a visible prompt (block modes)."""
        self._write_prompt(prompt)
        return self.read_line()

    def read_message(self, prompt: str) -> str:
        """Read one message, coalescing a pasted burst; piped input stays one line per message."""
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
    """Spend one rate-limit token, run the agent, print the answer."""
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
    print("\n" + _judged_human_reply(agent, question, answer) + "\n")


#: Recent (question, answer) pairs of this process, for the "do not repeat unasked" rule.
_RECENT_REPLIES: list[tuple[str, str]] = []
_RECENT_REPLIES_KEEP = 8


def _judged_human_reply(agent: object, question: str, answer: str) -> str:
    """Judge the reply before printing: a human phrase gets a human answer, a repeat silence."""
    from core.conversation_contract import judge_reply

    # The judge reads the RAW answer (contract sections mark a report); the formatted
    # reply is what gets shown when the verdict is ok.
    judgement = judge_reply(question, answer, tuple(_RECENT_REPLIES))
    reply = format_human_response(answer)
    log = getattr(agent, "log", None)
    if log is not None and (judgement.verdict != "ok" or judgement.observations):
        try:
            log.log("conversation_contract", {
                "verdict": judgement.verdict,
                "reasons": list(judgement.reasons),
                "observations": list(judgement.observations),
            })
        except Exception:  # noqa: BLE001, S110 — the journal must not break the conversation
            pass
    if judgement.verdict == "trim" and judgement.human_reply:
        reply = judgement.human_reply
    elif judgement.verdict == "repeat_silence":
        reply = "(я это уже говорил — молчу, чтобы не шуметь)"
    elif judgement.verdict == "silence":
        reply = "(мне нечего добавить)"
    _RECENT_REPLIES.append((question, answer))
    del _RECENT_REPLIES[:-_RECENT_REPLIES_KEEP]
    return reply


def _stdin_is_interactive() -> bool:
    """True when stdin is a terminal (the condition for paste coalescing).

    No stdin (pythonw.exe), a closed stream or a dead descriptor: no console; other errors raise.
    """
    stream = sys.stdin
    if stream is None:
        return False
    try:
        return bool(stream.isatty())
    except (OSError, ValueError):
        return False


def _run_handler(
    agent: object, label: str, handler: Callable[..., object], *args: object, **kwargs: object,
) -> object:
    """Run one handler or the agent call; a crash is reported in one line, not raised.

    A crash returns True (handled) so the text is not passed on to the agent.
    """
    try:
        return handler(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — one bad command must not end the session
        print(f"(command failed: {label}: {type(exc).__name__}: {exc})", file=sys.stderr)
        try:
            agent.log.log(  # type: ignore[attr-defined]
                "repl_command_error",
                {"command": label, "error_type": type(exc).__name__, "error": str(exc)},
            )
        except Exception:  # noqa: BLE001, S110 — already reported on stderr above
            pass  # nosec B110
        return True


# The check order below is pinned by the characterization tests; collaborators are called
# through their module so one monkeypatch is seen here and in cli/one_shot.py alike.


def run_repl(
    agent: object,
    *,
    reader: _StdinLineReader,
    rate_limiter: CLIRateLimiter,
    workspace: Path,
    file_hint: str | None = None,
) -> int:
    """Run the interactive dialogue until EOF/Ctrl+C and return the exit code."""
    while True:
        try:
            q = reader.read_message("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            # An empty Enter must not exit: the parent shell would run the rest of a paste.
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
            try:
                q = _collect_continuation(q, lambda: reader.prompt_line("... "))
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not q:
                # An empty message would spend a rate-limit token and ask nothing.
                continue
        if q == ":operator-task":
            print("(operator task block started; finish with :end)", file=sys.stderr)
            try:
                block = _collect_operator_task_block(lambda: reader.prompt_line("... "))
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            _run_handler(
                agent, ":operator-task",
                operator_task._handle_operator_task, block, agent, workspace,
            )
            continue
        # The buffer goes straight to the agent, bypassing the keyword router, so text that
        # merely mentions budget or approval is not hijacked by a shortcut.
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
            if _run_handler(
                agent, "operator reply",
                intent_bridge._handle_local_operator_reply, buffered, agent,
            ):
                continue
            _run_handler(
                agent, "agent run", _ask_the_agent, agent, buffered,
                rate_limiter=rate_limiter, workspace=workspace, file_hint=file_hint,
            )
            continue
        if q.startswith(":") or q == "?":
            if _run_handler(
                agent, q.split(maxsplit=1)[0],
                command_dispatch.handle_meta_command, q, agent, workspace,
            ):
                continue
            print(f"(unknown command: {q})", file=sys.stderr)
            continue
        if _run_handler(
            agent, "operator reply", intent_bridge._handle_local_operator_reply, q, agent,
        ):
            continue
        if _run_handler(
            agent, "operator intent",
            intent_bridge.handle_conversational_operator_input, q, agent, workspace,
        ):
            continue
        _run_handler(
            agent, "agent run", _ask_the_agent, agent, q,
            rate_limiter=rate_limiter, workspace=workspace, file_hint=file_hint,
        )
