"""CLI entry point for the agent MVP-5.

Interactive sessions have Working Memory (session-scoped turns) AND
Persistent Memory (long-term records on disk, gated by a Write Policy).
The planner + synthesizer see both prior turns and any retrieved long-term
records that share keywords with the current question.

Usage examples:
    # One-shot — no memory, fresh session
    python main.py --ask "How does Dijkstra's algorithm work?"

    # Interactive — multi-turn dialogue with both memories
    python main.py
    > What is DuckDuckGo?
    > And who founded it?                 # follow-up; planner reuses turn 1
    > :remember preference,fact I prefer concise answers in Russian
    > :ingest-source "docs/архитектура автономного Агента.txt"
    > :ingest-project . --limit 40 --dry-run
    > :source-library books
    > :ingest-web "autonomous agent" --sources wikis,science --limit 3 --dry-run
    > :ingest-rss https://www.python.org/blogs/rss/ --limit 5 --dry-run
    > :connectors
    > :connector-plan "monitor Python releases"
    > :memory                             # inspect working + persistent memory
    > :forget mem_abc123                  # delete one persistent record
    > :forget                             # delete ALL persistent records
    > :clear                              # wipe working memory only
    > :quit

    # Interactive with a file hint
    python main.py --file "docs/архитектура автономного Агента.txt"
    > How many domains are in the file?   # file_read runs
    > And what is in section 12.4?        # planner can reuse cached file artifact
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from app.io import _force_utf8_io
from core.approval import ApprovalProvider, AutoApprover, CLIApprovalProvider


# Parsers and small text helpers live in cli/parsers.py; re-exported here so
# existing imports (`from main import _parse_remember`, …) keep working.
from cli.parsers import (
    _compact_one_line,
    _env_bool,
    _parse_ingest_options,
    _parse_remember,
    _parse_repair_generation_args,
    _parse_source_planning_args,
    _resolve_workspace_text_file,
    _split_meta_args,
    _truncate_text,
)
# Budget / autonomy-readiness commands live in cli/commands_budget.py; the two
# hybrid handlers that also need the operator digest stay in main.py.
from cli.commands_budget import (
    _autonomy_readiness_payload,
    _budget_enforcement_status,
    _format_operator_budget_digest,
)
# Memory / hygiene / rollback commands live in cli/commands_memory.py
# (agent-method driven, no main back-references, so no cycle).
from cli.commands_memory import (
    _handle_hygiene,
    _handle_rollback,
    _print_persistent,
)
# Source ingestion / source-registry / planning commands live in
# cli/commands_ingest.py (no main back-references, so no cycle).
from cli.commands_ingest import _handle_self_build_propose
# The :help page and the REPL startup command summary are rendered from the
# command registry in cli/help.py (Phase 2 of the main.py extraction).
# Explicit ':command' dispatch lives in cli/command_dispatch.py; re-exported
# here so `from main import handle_meta_command` keeps working unchanged.
# Re-exported for tests that reach these through `main` (attribute access or
# monkeypatch by name); the REPL itself now goes through cli/command_dispatch.py.
from cli.commands_proposals import _handle_subagent_proposal
from cli.commands_self_apply import _handle_self_apply_run
# Paste-safe stdin ownership for the REPL lives in cli/repl.py; re-exported here
# because main() drives the loop and several tests reach these through `main`.
# --resume decision-making lives in cli/resume.py; the question builder is
# re-exported because tests/test_budget_resume.py imports it through `main`.
from cli.resume import _resume_question_from_checkpoint, resolve_resume
from cli.repl import (
    PASTE_COALESCE_GAP_SECONDS,
    _coalesce_burst,
    _collect_instruction_buffer,
    _StdinLineReader,
    _stdin_is_interactive,
    run_repl,
)
from cli.command_dispatch import handle_meta_command
# Plain-language -> command routing lives in cli/intent_bridge.py; re-exported
# here because tests and the REPL reach these through `main`.
from cli.intent_bridge import (
    _dispatch_operator_intent,
    _handle_local_operator_reply,
    _local_operator_reply,
    handle_conversational_operator_input,
)
from cli.args import build_parser
from cli.help import render_startup_commands
# The one-shot `--ask` run (memory-free agent, command precedence, deep
# escalation) lives in cli/one_shot.py; main() only decides which mode to enter.
from cli.one_shot import run_one_shot
from app.bootstrap import build_agent
# The budget guard (wrap agent.run so an exhausted model budget becomes a
# resumable paused checkpoint) lives in app/budget_guard.py; re-exported here so
# existing imports (`from main import _run_agent_with_budget_guard`, …) keep
# working exactly as before.
from app.budget_guard import _run_agent_with_budget_guard
from app.daemon_notice import _print_daemon_inbox_notice
from app.operator_status import (
    _format_next_actions,
    _runtime_capability_facts,
)
from app.operator_task import _handle_operator_task
from app.runtime_cli import _handle_auto_run
from app.task_scheduler_cli import _schedule_disable_message


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


def main() -> int:
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
    # precedence and deep escalation all live in cli/one_shot.py. The five
    # collaborators are passed in rather than imported there, so that
    # `monkeypatch.setattr(main, "build_agent", …)` and friends keep being
    # observed — see docs/refactor/CLI_BASELINE.md §2.5 and cli/one_shot.py.
    if args.ask:
        return run_one_shot(
            args.ask,
            workspace=workspace,
            file_hint=args.file,
            auto_approve=args.auto_approve,
            reason=args.reason,
            expect=args.expect,
            build_agent=build_agent,
            handle_meta_command=handle_meta_command,
            handle_local_operator_reply=_handle_local_operator_reply,
            handle_conversational=handle_conversational_operator_input,
            run_agent_with_budget_guard=_run_agent_with_budget_guard,
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
    # The dialogue loop itself lives in cli/repl.py. Same seam as one-shot: the
    # five collaborators are passed in so patches on `main` stay observable
    # (docs/refactor/CLI_BASELINE.md §2.5).
    return run_repl(
        agent,
        reader=_reader,
        rate_limiter=_rate_limiter,
        workspace=workspace,
        file_hint=args.file,
        handle_meta_command=handle_meta_command,
        handle_local_operator_reply=_handle_local_operator_reply,
        handle_conversational=handle_conversational_operator_input,
        run_agent_with_budget_guard=_run_agent_with_budget_guard,
        handle_operator_task=_handle_operator_task,
    )


if __name__ == "__main__":
    raise SystemExit(main())
