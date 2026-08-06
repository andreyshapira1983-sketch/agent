"""The CLI itself: parse, decide the mode, wire the session, hand off.

``run_cli`` is the whole of what ``main()`` used to be, moved here in the last
extraction step. It performs the steps below in this order. Parts of that
order are frozen by tests and parts are not, and the difference matters:
``test_cli_one_shot_policy.py`` pins ``load_dotenv`` before ``build_agent``
and pins both fast paths ahead of either; ``test_cli_mode_selection.py``
pins which mode is chosen, not the sequence. Steps 1-2 went unguarded until
2026-08-05, and that is exactly where they had drifted apart -- see the
comment at ``_force_utf8_io``.

1. ``_force_utf8_io()`` -- before argparse can print anything, because
   ``--help`` is written and exited from *inside* ``parse_args``;
2. parse the seven flags (``cli/args.py``);
3. the two pre-``load_dotenv()`` fast paths, ``:self-build-propose`` and
   ``:schedule-disable``, which must not build an agent or touch ``.env``;
4. ``load_dotenv()``;
5. ``--resume`` (``cli/resume.py``), which can exit before an agent exists;
6. the file-hint preflight, the only path that exits ``2`` after startup;
7. one-shot ``--ask`` (``cli/one_shot.py``) -- or
8. the interactive session: stdin reader, approval provider, agent, rate
   limiter, daemon notice, banner, then the loop in ``cli/repl.py``.

``main.py`` is now nothing but a launcher over this module -- 47 lines, with no
re-export block left; ``tests/characterization/test_main_public_surface.py``
is what keeps it that way.

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

    Measured 2026-08-05, before this was tightened: `--file ""`, `--file "   "`
    and a directory all passed. The first two never reached the check at all —
    `if not file_hint` is true for an empty string, so the function returned
    "fine" for a flag the operator did give. The third passed because
    `path.exists()` is true for a directory.

    `None` still means the flag was absent, which is the one legitimate way to
    have no hint. Path is NOT constrained to the workspace: that is a separate
    decision and is deliberately not taken here.
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
    # BEFORE `parse_args`, not after. argparse prints `--help` and raises
    # SystemExit from inside `parse_args`, so a call placed after it never
    # runs on that path: measured 2026-08-05, zero invocations on `--help`,
    # and the em dash in the parser's own description reached a cp1251
    # console as a replacement char. Nothing here depends on the parsed
    # arguments, so there is no reason for it to wait.
    _force_utf8_io()
    args = build_parser().parse_args()
    workspace = Path(args.workspace).resolve()
    ask_head = args.ask.lstrip() if args.ask else ""
    # Split on ANY whitespace, not on a literal space. A tab between the
    # command and its argument left `head` as the whole string, so a real
    # command reached the user as "(unknown command: ...)". Measured
    # 2026-08-05. `cli/command_dispatch.py` is changed the same way in the
    # same commit: fixing one path alone would make two of the 95 commands
    # behave differently from the other 93.
    _parts = ask_head.split(maxsplit=1)
    head = _parts[0] if _parts else ""
    rest = _parts[1] if len(_parts) > 1 else ""
    if head.lower() == ":self-build-propose":
        _handle_self_build_propose(rest.strip(), None, workspace)
        return 0
    if head.lower() == ":schedule-disable":
        print(_schedule_disable_message(rest.strip(), workspace), file=sys.stderr)
        return 0

    # From the WORKSPACE, not from wherever the process was launched.
    # Measured 2026-08-05: with a bare `load_dotenv()`, running from folder A
    # with `--workspace B` loaded A's `.env` — code and data from one project,
    # keys and settings from another. `agent_tick.py` already passes the path
    # (twice); the CLI had not caught up. Default `--workspace` is ".", so an
    # ordinary launch resolves to the same file it always did.
    load_dotenv(workspace / ".env")

    # §3.5 Resume: if --resume is given, look up the checkpoint file and
    # short-circuit before building the full agent stack when possible.
    #
    # `is not None`, а НЕ проверка истинности: argparse ставит `None`, когда
    # флага не было, и пустую строку, когда его дали пустым. При проверке
    # истинности `--resume ""` проваливался мимо всей ветки — оператор просил
    # возобновить, молча получал новый прогон с кодом 0, тогда как остальные
    # четыре негодных значения честно падали с кодом 2. Проверка в
    # `resolve_resume` пустую строку отвергает; до неё просто не доходило.
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

    # Same truthiness trap the `--resume` comment above describes, left standing
    # twelve lines below it. `--ask ""` made `if args.ask:` false, so the
    # operator asked for a one-shot answer and silently got an interactive REPL
    # instead — while `--resume ""` honestly exits 2. Measured 2026-08-05.
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
