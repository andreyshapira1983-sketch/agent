"""The CLI itself: parse, decide the mode, wire the session, hand off.

``run_cli`` is the whole startup sequence. The order below is the route, and
several places in it are load-bearing rather than incidental:

1. ``_force_utf8_io()`` -- must precede ``parse_args``, which writes ``--help``
   and raises ``SystemExit`` from inside itself;
2. parse the flags (``cli/args.py``);
3. the two pre-``load_dotenv()`` fast paths, ``:self-build-propose`` and
   ``:schedule-disable`` -- neither may build an agent or read ``.env``;
4. ``load_dotenv(workspace / ".env")`` -- the workspace's file, not the launch
   directory's;
5. ``--resume`` (``cli/resume.py``), which can exit before an agent exists and
   may replace ``ask`` / ``file_hint`` with the checkpoint's;
6. reject an empty ``--ask``, then the file-hint preflight -- the two paths
   that exit ``2`` without spending anything;
7. one-shot ``--ask`` (``cli/one_shot.py``) -- or
8. the interactive session: stdin reader, approval provider, agent, rate
   limiter, daemon notice, banner, then the loop in ``cli/repl.py``.

What the tests fix: ``test_cli_one_shot_policy.py`` pins step 1 before 2,
``load_dotenv`` before ``build_agent``, and both fast paths ahead of either;
``test_cli_mode_selection.py`` pins which mode is chosen and the exit codes of
step 6; ``test_cli_command_precedence.py`` pins that both dispatch paths split
a command on any whitespace, not on a literal space.

``main.py`` is a launcher over this module and nothing else;
``tests/characterization/test_main_public_surface.py`` keeps it that way.

**Where to patch in tests.** Steps 1-8 are performed *here*, so a fake for
``build_agent``, ``load_dotenv``, ``_StdinLineReader``, ``CLIApprovalProvider``,
``_print_daemon_inbox_notice``, ``_schedule_disable_message`` or
``_handle_self_build_propose`` belongs on ``cli.app`` -- patching ``main``
intercepts nothing, and ``tests/characterization/test_main_patch_seams.py``
fails loudly if a fake is left there. ``agent_tick.py`` and ``api/server.py``
build their own agents through ``app.bootstrap``, so *their* fakes go on that
module.
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from app.bootstrap import build_agent
from app.daemon_notice import _print_daemon_inbox_notice
from app.io import _force_utf8_io
from app.task_scheduler_cli import _schedule_disable_message
from cli.args import build_parser
from cli.commands_ingest import _handle_self_build_propose
from cli.help import render_startup_commands
from cli.one_shot import run_one_shot
from cli.repl import _stdin_is_interactive, _StdinLineReader, run_repl
from cli.resume import resolve_resume
from core.approval import ApprovalProvider, AutoApprover, CLIApprovalProvider


def _preflight_file_hint(file_hint: str | None, workspace: Path) -> tuple[bool, str | None]:
    """`--file` must name an existing FILE. Three ways it may not, each named.

    `None` means the flag was absent — the one legitimate way to have no hint.
    An empty or whitespace-only value is a flag the operator DID give, and is
    refused: falsiness cannot tell those two apart, so the check is `is None`.

    The three refusals carry different messages on purpose. "empty path", "is a
    directory" and "does not exist" are different faults, and an operator
    reading stderr must not have to guess which one happened.

    The path is NOT constrained to the workspace. That is a separate decision,
    deliberately not taken here.
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
    # Before `parse_args`: argparse writes `--help` and raises SystemExit from
    # inside it, so anything placed after would never run on that path — and
    # the help text is where a non-ASCII byte first meets the console.
    _force_utf8_io()
    args = build_parser().parse_args()
    workspace = Path(args.workspace).resolve()
    ask_head = args.ask.lstrip() if args.ask else ""
    # Any whitespace, not a literal space: a pasted command can arrive with a
    # tab. `cli/command_dispatch.py` splits the same way, and must — these two
    # fast paths and the other 93 commands have to agree on what a command is.
    _parts = ask_head.split(maxsplit=1)
    head = _parts[0] if _parts else ""
    rest = _parts[1] if len(_parts) > 1 else ""
    if head.lower() == ":self-build-propose":
        _handle_self_build_propose(rest.strip(), None, workspace)
        return 0
    if head.lower() == ":schedule-disable":
        print(_schedule_disable_message(rest.strip(), workspace), file=sys.stderr)
        return 0

    # The workspace's file, named explicitly. A bare `load_dotenv()` reads the
    # launch directory, which lets keys come from one project while code and
    # data come from another. `agent_tick.py` names the path for the same
    # reason. Default `--workspace` is ".", so an ordinary launch is unchanged.
    load_dotenv(workspace / ".env")

    # §3.5 Resume: look up the checkpoint and short-circuit before the full
    # agent stack is built, when possible.
    #
    # `is not None`, not truthiness: argparse gives `None` for an absent flag
    # and "" for an empty one. Falsiness merges those, and the empty case must
    # reach `resolve_resume`, which is what rejects it.
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

    # Same distinction as `--resume` above, and for the same reason: falsiness
    # would send an empty `--ask` into the interactive branch, answering a
    # request for one-shot output with a prompt. Checked AFTER resume, so a
    # checkpoint may supply the question.
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

    # One-shot mode. Provider selection, the memory-free agent build, command
    # precedence and deep escalation all live in cli/one_shot.py, which reaches
    # its collaborators through their own modules. `build_agent` is handed over
    # from here so that one fake on `cli.app` covers both modes.
    if args.ask is not None:
        return run_one_shot(
            args.ask,
            workspace=workspace,
            file_hint=args.file,
            auto_approve=args.auto_approve,
            reason=args.reason,
            expect=args.expect,
            build_agent=build_agent,
        )

    # ── Paste-safe interactive input ─────────────────────────────────────────
    # One background reader owns stdin so the top-level prompt, block modes,
    # and the approval prompt all pull from the same queue. This is what lets
    # a pasted multi-line block be coalesced into ONE message instead of being
    # chopped into many separate questions.
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

    # Interactive — single agent with working + persistent memory + approval UX
    agent = build_agent(workspace, with_memory=True, approval_provider=approval_provider)

    # ── Session rate limiter (T8 / §6) ────────────────────────────────────────
    # Prevents runaway or accidental rapid-fire requests from burning budget or
    # triggering external API rate limits.  30 requests per 60 s is generous
    # for human-paced interaction but catches programmatic loops.
    from core.rate_limiter import CLIRateLimiter
    _rate_limiter = CLIRateLimiter(max_requests=30, window_seconds=60.0)

    # ── Daemon wake-up notice ─────────────────────────────────────────────────
    # If the background daemon ran while the user was away and found problems
    # (failed tests, repair proposals), surface them immediately so the user
    # sees them before the first prompt.
    _print_daemon_inbox_notice(workspace)

    print(
        f"Agent ready. file_hint={args.file or '-'}  memory=on  persistent=on  "
        f"approval={type(approval_provider).__name__}. "
        + render_startup_commands(),
        file=sys.stderr,
    )
    # The dialogue loop itself lives in cli/repl.py and reaches its
    # collaborators through their own modules; nothing is handed over from here.
    return run_repl(
        agent,
        reader=_reader,
        rate_limiter=_rate_limiter,
        workspace=workspace,
        file_hint=args.file,
    )
