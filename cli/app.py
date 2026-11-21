"""CLI entry: parse the flags, choose the mode (fast path, resume, one-shot, REPL) and hand off."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.bootstrap import build_agent
from app.daemon_notice import _print_daemon_inbox_notice
from app.io import _force_utf8_io
from app.task_scheduler_cli import _schedule_disable_message
from cli.args import build_parser
from cli.commands_self_build import _handle_self_build_propose
from cli.help import render_startup_commands
from cli.one_shot import read_history, run_one_shot
from cli.repl import _stdin_is_interactive, _StdinLineReader, run_repl
from cli.resume import resolve_resume
from core.approval import ApprovalProvider, AutoApprover, CLIApprovalProvider


def _preflight_file_hint(file_hint: str | None, workspace: Path) -> tuple[bool, str | None]:
    """Check that ``--file`` names an existing file; return (ok, error message).

    None means the flag was absent; an empty value was given, so it is refused.
    """
    if file_hint is None:
        return True, None

    stripped = file_hint.strip()
    if not stripped:
        return (
            False,
            ("ERROR: --file was given an empty path.\n\n"
             "No model calls were made."),
        )

    path = Path(stripped.replace("\\", "/"))
    if not path.is_absolute():
        path = workspace / path
    path = path.resolve()

    if path.is_file():
        return True, None
    if path.is_dir():
        return (
            False,
            ("ERROR: file hint is a directory, not a file:\n"
             f"{path}\n\n"
             "No model calls were made."),
        )
    return (
        False,
        ("ERROR: file hint does not exist:\n"
        f"{path}\n\n"
        "No model calls were made."),
    )


def run_cli() -> int:
    """Run the command-line interface; return the process exit code."""
    # Must precede parse_args: --help prints and exits from inside it.
    _force_utf8_io()
    args = build_parser().parse_args()
    workspace = Path(args.workspace).resolve()
    ask_head = args.ask.lstrip() if args.ask else ""
    # Split on any whitespace, as cli/command_dispatch.py does: a pasted command may hold a tab.
    _parts = ask_head.split(maxsplit=1)
    head = _parts[0] if _parts else ""
    rest = _parts[1] if len(_parts) > 1 else ""
    # These two fast paths run before load_dotenv: they must not read .env or build an agent.
    if head.lower() == ":self-build-propose":
        _handle_self_build_propose(rest.strip(), None, workspace)
        return 0
    if head.lower() == ":schedule-disable":
        print(_schedule_disable_message(rest.strip(), workspace), file=sys.stderr)
        return 0

    # The workspace's .env, not the launch directory's: keys must match the code and data in use.
    load_dotenv(workspace / ".env")
    # The entry point, not the library, declares where the silent-model roster lives.
    from core.llm import ensure_roster_home
    ensure_roster_home(workspace)

    # Likewise for the reasoning roster: a default inside the library collected entries
    # from every truncated call, test runs included. Scoped to this workspace.
    os.environ.setdefault(
        "AGENT_REASONING_ROSTER", str(workspace / "data" / "reasoning_roster.jsonl"),
    )

    # `is not None`: an empty --resume must reach resolve_resume, which rejects it.
    if args.resume is not None:
        decision = resolve_resume(
            args.resume,
            workspace=workspace,
            ask=args.ask,
            file_hint=args.file,
        )
        if decision.exit_code is not None:
            return decision.exit_code
        args.ask = decision.ask
        args.file = decision.file_hint
        resumed_from = decision.resumed_paused_trace
    else:
        resumed_from = None

    # After resume (a checkpoint may supply the question); an empty --ask must not open the REPL.
    if args.ask is not None and not args.ask.strip():
        print(
            "ERROR: --ask was given an empty question.\n\n"
            "No model calls were made.",
            file=sys.stderr,
        )
        return 2

    file_hint_ok, file_hint_error = _preflight_file_hint(args.file, workspace)
    if not file_hint_ok:
        print(file_hint_error, file=sys.stderr)
        return 2

    # build_agent is passed in so that one fake on cli.app covers both modes.
    if args.ask is not None:
        return run_one_shot(
            args.ask,
            workspace=workspace,
            file_hint=args.file,
            auto_approve=args.auto_approve,
            reason=args.reason,
            expect=args.expect,
            resumed_from=resumed_from,
            build_agent=build_agent,
            history=read_history(args.history),
            with_persistent=bool(getattr(args, "with_persistent", False)),
        )

    # One reader owns stdin for every prompt, so a pasted multi-line block stays one message.
    _reader = _StdinLineReader(interactive=_stdin_is_interactive())

    if args.auto_approve == "approve":
        approval_provider: ApprovalProvider = AutoApprover(default="approve")
    elif args.auto_approve == "deny":
        approval_provider = AutoApprover(default="deny")
    else:
        def _approval_input(prompt: str) -> str:
            sys.stderr.write(prompt)
            sys.stderr.flush()
            return _reader.read_line()

        approval_provider = CLIApprovalProvider(input_fn=_approval_input)

    agent = build_agent(workspace, with_memory=True, approval_provider=approval_provider)

    # Generous for a human, but stops runaway loops from burning budget or API limits.
    from core.rate_limiter import CLIRateLimiter
    _rate_limiter = CLIRateLimiter(max_requests=30, window_seconds=60.0)

    _print_daemon_inbox_notice(workspace)

    print(
        f"Agent ready. file_hint={args.file or '-'}  memory=on  persistent=on  "
        f"approval={type(approval_provider).__name__}. "
        + render_startup_commands(),
        file=sys.stderr,
    )
    return run_repl(
        agent,
        reader=_reader,
        rate_limiter=_rate_limiter,
        workspace=workspace,
        file_hint=args.file,
    )
