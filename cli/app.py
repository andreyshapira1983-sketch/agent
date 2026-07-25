"""The CLI itself: parse, decide the mode, wire the session, hand off.

``run_cli`` is the whole of what ``main()`` used to be, moved here in the last
extraction step. It performs, in this exact order (the order is a contract --
``tests/characterization/test_cli_mode_selection.py`` and
``test_cli_one_shot_policy.py`` freeze it):

1. parse the seven flags (``cli/args.py``);
2. ``_force_utf8_io()`` -- before any non-ASCII byte can flow through stdio;
3. the two pre-``load_dotenv()`` fast paths, ``:self-build-propose`` and
   ``:schedule-disable``, which must not build an agent or touch ``.env``;
4. ``load_dotenv()``;
5. ``--resume`` (``cli/resume.py``), which can exit before an agent exists;
6. the file-hint preflight, the only path that exits ``2`` after startup;
7. one-shot ``--ask`` (``cli/one_shot.py``) -- or
8. the interactive session: stdin reader, approval provider, agent, rate
   limiter, daemon notice, banner, then the loop in ``cli/repl.py``.

``main.py`` is now nothing but a launcher over this module -- 47 lines, with no
re-export block left (``docs/refactor/MAIN_SURFACE_AUDIT.md`` records how that
surface was retired).

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
from cli.repl import _StdinLineReader, _stdin_is_interactive, run_repl
from cli.resume import resolve_resume
from core.approval import ApprovalProvider, AutoApprover, CLIApprovalProvider


def _preflight_file_hint(file_hint: str | None, workspace: Path) -> tuple[bool, str | None]:
    if not file_hint:
        return True, None
    path = Path(file_hint.strip().replace("\\", "/"))
    if not path.is_absolute():
        path = workspace / path
    path = path.resolve()
    if path.exists():
        return True, None
    return (
        False,
        "ERROR: file hint does not exist:\n"
        f"{path}\n\n"
        "No model calls were made.",
    )


def run_cli() -> int:
    args = build_parser().parse_args()

    # Must run BEFORE any non-ASCII input flows through stdin / out.
    _force_utf8_io()
    workspace = Path(args.workspace).resolve()
    ask_head = args.ask.lstrip() if args.ask else ""
    head, _, rest = ask_head.partition(" ")
    if head.lower() == ":self-build-propose":
        _handle_self_build_propose(rest.strip(), None, workspace)  # type: ignore[arg-type]
        return 0
    if head.lower() == ":schedule-disable":
        print(_schedule_disable_message(rest.strip(), workspace), file=sys.stderr)
        return 0

    load_dotenv()

    # §3.5 Resume: if --resume is given, look up the checkpoint file and
    # short-circuit before building the full agent stack when possible.
    if args.resume:
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

    file_hint_ok, file_hint_error = _preflight_file_hint(args.file, workspace)
    if not file_hint_ok:
        print(file_hint_error, file=sys.stderr)
        return 2

    # One-shot mode. Provider selection, the memory-free agent build, command
    # precedence and deep escalation all live in cli/one_shot.py, which reaches
    # its collaborators through their own modules. `build_agent` is handed over
    # from here so that one fake on `cli.app` covers both modes.
    if args.ask:
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
