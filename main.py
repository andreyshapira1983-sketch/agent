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

import argparse
import json
import os
import queue
import re
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

from app.io import _force_utf8_io
from core.approval import ApprovalProvider, AutoApprover, CLIApprovalProvider
from core.subagent_memory_scope import (
    needs_delegation,
    propose_subagent,
)
from core.loop import format_human_response


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
    _budget_ledger_snapshot,
    _format_operator_budget_digest,
    _handle_budget_config,
    _handle_budget_kill_switch,
    _handle_budget_status,
    _handle_budget_window_status,
    _next_action_prerequisites,
    _persistent_budget_limits_configured,
)
# Approval-inbox / alert-ack / best-next-action commands live in
# cli/commands_approval.py (no main back-references, so no import cycle).
from cli.commands_approval import (
    _approval_inbox_for,
    _handle_alert_ack,
    _handle_alert_ack_clear,
    _handle_alert_ack_list,
    _handle_approval_abort,
    _handle_approval_decision,
    _handle_approval_list,
    _handle_approval_run,
    _handle_approval_triage,
    _handle_best_next_action,
    _handle_self_issue_verify,
)
# Memory / hygiene / rollback commands live in cli/commands_memory.py
# (agent-method driven, no main back-references, so no cycle).
from cli.commands_memory import (
    _handle_hygiene,
    _handle_memory_consolidate,
    _handle_rollback,
    _handle_smart_memory,
    _print_persistent,
)
# Model routing / registry / catalog / usage commands live in
# cli/commands_models.py (model_router-driven, no main back-references).
from cli.commands_models import (
    _handle_model_discovery_audit,
    _handle_model_registry_audit,
    _handle_model_usage,
    _handle_models,
    _handle_provider_catalog_refresh,
    _handle_refresh_models,
)
# Misc operator commands (audit / learn / team / connectors) live in
# cli/commands_misc.py (no main back-references, so no cycle).
from cli.commands_misc import (
    _handle_architecture_audit,
    _handle_assumptions,
    _handle_conflicts,
    _handle_connector_plan,
    _handle_connectors,
    _handle_learn,
    _handle_release_audit,
    _handle_state_store_drill,
    _handle_supply_chain_audit,
    _handle_team_plan,
    _handle_team_run,
)
# Source ingestion / source-registry / planning commands live in
# cli/commands_ingest.py (no main back-references, so no cycle).
from cli.commands_ingest import (
    _handle_implementation_plan,
    _handle_ingest_project,
    _handle_ingest_rss,
    _handle_ingest_source,
    _handle_ingest_web,
    _handle_patch_proposal_plan,
    _handle_self_build_propose,
    _handle_source_library,
    _handle_source_registry,
    _handle_source_review_plan,
    _self_build_propose_payload,
)
# Self-repair commands live in cli/commands_repair.py (no main back-references).
from cli.commands_repair import _handle_propose_repair, _handle_repair
# :self-apply-run bridges an approved inbox item into the trusted self-apply
# lane (cli/commands_self_apply.py, no main back-references).
from cli.commands_self_apply import _handle_self_apply_run
# :self-build-produce runs the subagent producer that generates one low-risk
# self-apply proposal into the approval inbox (cli/commands_self_build.py).
# :self-split plans one deterministic (no-LLM) incremental extraction step for
# an oversized module and publishes it as a self-apply approval item.
from cli.commands_self_split import _handle_self_split
# :self-task-propose runs the Stage-A coding-task producer that turns a real code
# TODO/FIXME into a task + failing acceptance test approval item
# (cli/commands_self_task.py, roadmap Stage 1).
# :self-task-build implements one APPROVED coding task (Stage B): it writes code
# to make the frozen acceptance test pass and proposes it to the self-apply lane
# (cli/commands_self_task.py, roadmap Stage 1).
from cli.commands_self_task import _handle_self_task_build
# :value-review / :value-review-list capture a human value verdict for an applied
# self-build proposal (cli/commands_value_review.py, TD-032, capture-only).
from cli.commands_value_review import (
    _handle_value_review,
    _handle_value_review_list,
)
# Local read-only health panel command.
from cli.commands_health import _handle_dry_health_pass
# :capability-request / :subagent-proposal — both file a human-approved
# proposal and nothing else (cli/commands_proposals.py).
from cli.commands_proposals import (
    _handle_capability_request,
    _handle_subagent_proposal,
)
# The :help page and the REPL startup command summary are rendered from the
# command registry in cli/help.py (Phase 2 of the main.py extraction).
# Explicit ':command' dispatch lives in cli/command_dispatch.py; re-exported
# here so `from main import handle_meta_command` keeps working unchanged.
from cli.command_dispatch import handle_meta_command
# Plain-language -> command routing lives in cli/intent_bridge.py; re-exported
# here because tests and the REPL reach these through `main`.
from cli.intent_bridge import (
    _dispatch_operator_intent,
    _handle_local_operator_reply,
    _local_operator_reply,
    handle_conversational_operator_input,
)
from cli.help import render_help, render_startup_commands
from app.bootstrap import build_agent
# The budget guard (wrap agent.run so an exhausted model budget becomes a
# resumable paused checkpoint) lives in app/budget_guard.py; re-exported here so
# existing imports (`from main import _run_agent_with_budget_guard`, …) keep
# working exactly as before.
from app.budget_guard import (
    _budget_block_payload,
    _existing_paused_checkpoint,
    _persist_resumable_budget_stop,
    _run_agent_with_budget_guard,
    _workspace_from_agent,
)
from app.daemon_notice import _print_daemon_inbox_notice
from app.operator_status import (
    _format_next_actions,
    _handle_autonomy_readiness,
    _handle_next_safe_test,
    _handle_operator_capability_check,
    _handle_operator_gaps_check,
    _handle_operator_weakness_finder,
    _handle_programming_readiness,
    _runtime_capability_facts,
    _handle_next_actions,
    _handle_operator_budget,
    _handle_operator_check,
    _handle_urgent_status,
    _operator_digest_payload,
)
from app.operator_task import _handle_operator_task
from app.runtime_cli import (
    _handle_auto_run,
    _handle_auto_status,
    _handle_campaign_start,
    _handle_campaign_status,
    _handle_work_session,
)
from app.task_scheduler_cli import (
    _handle_queue_status,
    _handle_schedule_add,
    _handle_schedule_disable,
    _handle_schedule_list,
    _handle_schedule_tick,
    _handle_scheduler_status,
    _handle_task_add,
    _handle_task_cancel,
    _handle_task_list,
    _handle_task_run,
    _schedule_disable_message,
    _scheduler_for,
    _task_queue_for,
)


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


def _resume_question_from_checkpoint(ctx) -> str:
    paused = getattr(ctx, "paused", None) or {}
    if not paused:
        return ctx.question
    original = str(paused.get("original_user_question") or ctx.question)
    planned = paused.get("planned_steps") or []
    completed = paused.get("completed_steps") or []
    remaining = paused.get("remaining_steps") or []
    blocked = paused.get("blocked_model") or {}
    return "\n".join(
        [
            "Resume the interrupted task from this saved budget checkpoint.",
            f"Original user question: {original}",
            f"Active goal: {paused.get('active_goal') or original}",
            f"Interrupted phase: {paused.get('current_phase') or ctx.last_phase}",
            f"Stop reason: {paused.get('stop_reason') or 'budget_exhausted'}",
            f"Blocked model/counter: {json.dumps(blocked, ensure_ascii=False)}",
            f"Completed steps: {json.dumps(completed, ensure_ascii=False)}",
            f"Remaining steps: {json.dumps(remaining, ensure_ascii=False)}",
            f"Planned steps: {json.dumps(planned, ensure_ascii=False)}",
            "Continue from the remaining steps when they are still relevant. "
            "Do not repeat completed discovery unless it must be refreshed.",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Autonomous agent MVP-4 — LLM picks tools, sessions have working memory."
        )
    )
    parser.add_argument(
        "--ask",
        help="One-shot question (no memory). Omit to enter the interactive REPL.",
    )
    parser.add_argument(
        "--file",
        help=(
            "Optional file hint. The planner MAY call file_read with it. "
            "Without this hint, file_read is never used."
        ),
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root (default: current directory).",
    )
    parser.add_argument(
        "--auto-approve",
        choices=["off", "approve", "deny"],
        default="off",
        help=(
            "Approval policy for escalated (irreversible / external) actions: "
            "'off' (default) = interactive prompts in the REPL, deny in one-shot; "
            "'approve' = auto-approve everything (use only in tests / scripts); "
            "'deny' = auto-deny everything."
        ),
    )
    parser.add_argument(
        "--resume",
        metavar="TRACE_ID",
        default=None,
        help=(
            "Resume a previous run by trace ID. If the run completed synthesis, "
            "the cached answer is printed immediately (no LLM call). "
            "Budget-paused runs resume with saved phase/step context; "
            "crash-partial runs are re-run from scratch."
        ),
    )
    parser.add_argument(
        "--reason",
        default=None,
        help=(
            "Deep/Opus escalation reason (one-shot --ask only). Without it, a "
            "deep request downgrades to the standard model — the agent never "
            "opens Opus for itself. Valid: operator_explicitly_requested_opus, "
            "planner_multi_file_architecture_change."
        ),
    )
    parser.add_argument(
        "--expect",
        default=None,
        help=(
            "Expected deep output (used with --reason). Valid: minimal_patch_plan, "
            "architecture_tradeoff, cross_file_synthesis, final_answer_high_stakes."
        ),
    )
    args = parser.parse_args()

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
        import re as _re
        # Mirror the allowlist that CheckpointWriter uses: alphanumerics plus
        # hyphens and underscores only.  Reject anything with slashes, dots,
        # or other characters that could produce a path-traversal when the
        # loader constructs its file path (core/checkpoint.py:166).
        _SAFE_TRACE_RE = _re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$')
        if not _SAFE_TRACE_RE.match(args.resume):
            print(
                f"[resume] Invalid trace_id {args.resume!r}: "
                "only letters, digits, hyphens and underscores are allowed.",
                file=sys.stderr,
            )
            return 2
        from core.checkpoint import CheckpointLoader as _CPLoader, PHASE_PAUSED as _PHASE_PAUSED
        _log_dir = workspace / "logs"
        _loader = _CPLoader(_log_dir)
        _ctx = _loader.load(args.resume)
        if _ctx is None:
            print(
                f"[resume] No usable checkpoint found for trace_id={args.resume!r}. "
                "Running fresh.",
                file=sys.stderr,
            )
        elif _ctx.last_phase == _PHASE_PAUSED and _ctx.paused:
            print(
                f"[resume] Resuming budget-paused checkpoint "
                f"trace_id={args.resume!r} phase={_ctx.paused.get('current_phase')!r}.",
                file=sys.stderr,
            )
            if not args.ask:
                args.ask = _resume_question_from_checkpoint(_ctx)
            if not args.file:
                args.file = _ctx.file_hint
        elif _ctx.answer is not None:
            # Full cycle completed previously — replay the cached answer.
            print(
                f"[resume] Replaying cached answer for trace_id={args.resume!r} "
                f"(phase={_ctx.last_phase}, artifacts={list(_ctx.artifacts)})",
                file=sys.stderr,
            )
            print("\n" + format_human_response(_ctx.answer) + "\n")
            return 0
        else:
            # Cycle did not complete — fall through to a normal run so the
            # agent re-tries from scratch (safe default).
            print(
                f"[resume] Checkpoint found but synthesis incomplete "
                f"(last_phase={_ctx.last_phase!r}). Re-running from scratch.",
                file=sys.stderr,
            )
            if not args.ask:
                args.ask = _ctx.question
            if not args.file:
                args.file = _ctx.file_hint

    file_hint_ok, file_hint_error = _preflight_file_hint(args.file, workspace)
    if not file_hint_ok:
        print(file_hint_error, file=sys.stderr)
        return 2

    # Approval provider selection. One-shot can't realistically prompt a
    # human, so it falls back to AutoApprover unless the user opted in via
    # --auto-approve. Interactive uses the live CLI prompt by default.
    if args.ask:
        if args.auto_approve == "approve":
            approval_provider: ApprovalProvider = AutoApprover(default="approve")
        elif args.auto_approve == "deny":
            approval_provider = AutoApprover(default="deny")
        else:
            # 'off' in one-shot = no provider wired = escalated tools blocked.
            approval_provider = None

        # with_persistent=False: one-shot must NOT read or mutate
        # data/persistent_memory.jsonl — the docstring at line 9 promises
        # "no memory, fresh session", so persistent memory must be excluded
        # too, not just working (session) memory.
        agent = build_agent(
            workspace,
            with_memory=False,
            with_persistent=False,
            approval_provider=approval_provider,
        )
        # Explicit ':' meta-commands take precedence over fuzzy intent routing,
        # mirroring the interactive REPL — otherwise e.g. ':campaign-start
        # --max-cost-units 0' is misread as a budget query by the classifier.
        ask_head = args.ask.lstrip()
        if ask_head.startswith(":") or ask_head == "?":
            if handle_meta_command(ask_head, agent, workspace):
                return 0
            print(f"(unknown command: {ask_head})", file=sys.stderr)
            return 0
        if _handle_local_operator_reply(args.ask, agent):
            return 0
        if handle_conversational_operator_input(args.ask, agent, workspace):
            return 0
        # Deep/Opus escalation is opt-in and operator-driven: only an explicit
        # --reason (with --expect) lets planner/synthesizer reach the deep tier.
        # Without it, deep_escalation stays None and every deep request
        # downgrades to the standard model.
        deep_escalation = None
        if args.reason or args.expect:
            from core.deep_escalation import OperatorEscalation
            deep_escalation = OperatorEscalation(
                reason=args.reason,
                expected_output=args.expect,
            )
        # stream=False: the formatted print below is the sole output.
        # With stream=True the raw Output-Contract tokens arrive first, then
        # format_human_response reprints the same content — double output.
        answer = _run_agent_with_budget_guard(
            agent,
            user_question=args.ask,
            file_hint=args.file,
            workspace=workspace,
            stream=False,
            deep_escalation=deep_escalation,
        )
        print("\n" + format_human_response(answer) + "\n")
        return 0

    # ── Paste-safe interactive input ─────────────────────────────────────────
    # One background reader owns stdin so the top-level prompt, block modes,
    # and the approval prompt all pull from the same queue. This is what lets
    # a pasted multi-line block be coalesced into ONE message instead of being
    # chopped into many separate questions.
    _reader = _StdinLineReader(interactive=_stdin_is_interactive())

    if args.auto_approve == "approve":
        approval_provider = AutoApprover(default="approve")
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
    while True:
        try:
            q = _reader.read_message("> ").strip()
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
                    bline = _reader.prompt_line("... ")
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
                    cline = _reader.prompt_line("... ")
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
                    line = _reader.prompt_line("... ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
                if line.strip().lower() == ":end":
                    break
                block_lines.append(line)
            _handle_operator_task("\n".join(block_lines), agent, workspace)
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
                    lambda: _reader.prompt_line("... ")
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
            if _handle_local_operator_reply(buffered, agent):
                continue
            rl = _rate_limiter.consume()
            if not rl.allowed:
                print(
                    f"(rate limit: too many requests — "
                    f"retry in {rl.retry_after_seconds:.1f}s, "
                    f"tokens remaining: {rl.tokens_remaining:.2f})",
                    file=sys.stderr,
                )
                continue
            answer = _run_agent_with_budget_guard(
                agent,
                user_question=buffered,
                file_hint=args.file,
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
        if _handle_local_operator_reply(q, agent):
            continue
        if handle_conversational_operator_input(q, agent, workspace):
            continue
        # ── Rate-limit check ─────────────────────────────────────────────────
        rl = _rate_limiter.consume()
        if not rl.allowed:
            print(
                f"(rate limit: too many requests — "
                f"retry in {rl.retry_after_seconds:.1f}s, "
                f"tokens remaining: {rl.tokens_remaining:.2f})",
                file=sys.stderr,
            )
            continue
        answer = _run_agent_with_budget_guard(
            agent,
            user_question=q,
            file_hint=args.file,
            workspace=workspace,
            stream=False,
        )
        print("\n" + format_human_response(answer) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
