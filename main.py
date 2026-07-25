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
# The CLI itself — argument parsing, mode selection, session wiring — lives in
# cli/app.py. main() is a launcher over it; everything else imported below is
# re-exported for `from main import …` callers (see
# docs/refactor/MAIN_SURFACE_AUDIT.md), not used by this file.
from cli.app import _preflight_file_hint, run_cli
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


def main() -> int:
    """Launch the CLI. Everything it does lives in cli/app.py."""
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
