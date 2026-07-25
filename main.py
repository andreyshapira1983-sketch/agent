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
from core.capability_request import propose_capability_request
from core.autonomous_runtime import AutonomousRuntime
from core.subagent_memory_scope import (
    needs_delegation,
    propose_subagent,
)
from core.subagent_contract import approval_payload_from_proposal
from core.loop import AgentLoop, format_human_response
from core.model_router import ModelRole
from core.intent_understanding import understand_intent
from core.operator_intent import OperatorIntent, route_operator_intent
from core.strategy_router import classify_operator_strategy


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
from cli.commands_self_build import _handle_self_build_produce, _handle_self_build_supervisor
# :self-split plans one deterministic (no-LLM) incremental extraction step for
# an oversized module and publishes it as a self-apply approval item.
from cli.commands_self_split import _handle_self_split
# :self-task-propose runs the Stage-A coding-task producer that turns a real code
# TODO/FIXME into a task + failing acceptance test approval item
# (cli/commands_self_task.py, roadmap Stage 1).
from cli.commands_self_task import _handle_self_task_propose
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
# The :help page and the REPL startup command summary are rendered from the
# command registry in cli/help.py (Phase 2 of the main.py extraction).
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


_REPLY_ONLY_STOP_RE = re.compile(
    r"\breply\s+only\s+with:\s*(?:\"([^\"]+)\"|'([^']+)'|“([^”]+)”)",
    re.IGNORECASE | re.DOTALL,
)


def _local_operator_reply(text: str, agent: AgentLoop | None = None) -> str | None:
    """Return a local response for explicit stop/ack operator instructions.

    TD-001: some operator-control messages are intentionally local and must not
    enter Planner/Synthesizer. Keep this narrow: only honour a quoted
    "Reply only with:" directive when the same instruction explicitly forbids
    the expensive model path.
    """
    normalized = " ".join((text or "").casefold().split())
    if "reply only with:" not in normalized:
        return None
    llm_stop_markers = (
        "do not call planner",
        "do not call planner or synthesizer",
        "do not use claude",
        "do not make a plan with llm",
        "не вызывай planner",
        "не вызывай synthesizer",
        "не используй claude",
        "не использовать claude",
    )
    if not any(marker in normalized for marker in llm_stop_markers):
        return None
    match = _REPLY_ONLY_STOP_RE.search(text)
    if match is None:
        return None
    answer = next((part for part in match.groups() if part), "").strip()
    if not answer:
        return None
    if agent is not None:
        agent.log.log(
            "local_operator_reply",
            {"reason": "reply_only_stop_instruction", "answer_preview": answer[:120]},
        )
    return answer


def _handle_local_operator_reply(text: str, agent: AgentLoop) -> bool:
    answer = _local_operator_reply(text, agent)
    if answer is None:
        return False
    print("\n" + format_human_response(answer) + "\n")
    return True


def _handle_capability_request(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Handle :capability-request — propose missing capability boundaries."""
    tokens = _split_meta_args(rest)
    submit = False
    as_json = False
    goal_parts: list[str] = []

    for token in tokens:
        if token == "--submit":
            submit = True
        elif token == "--json":
            as_json = True
        else:
            goal_parts.append(token)

    goal = " ".join(goal_parts).strip()
    if not goal:
        print("Usage: :capability-request <goal> [--submit] [--json]", file=sys.stderr)
        return True

    try:
        request = propose_capability_request(goal)
    except ValueError as exc:
        print(f"(capability-request failed: {exc})", file=sys.stderr)
        return True

    agent.log.log("capability_request_proposed", request.to_dict())
    if as_json:
        print(json.dumps(request.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(request.user_summary(), file=sys.stderr)

    if submit:
        inbox = _approval_inbox_for(agent, workspace)
        item = inbox.add(
            operation="capability_request",
            summary=f"Capability request: {request.capability_type} — {goal}",
            risk="external",
            reasons=(request.why_needed, request.human_risk_summary),
            payload=request.to_dict(),
        )
        print(f"(capability-request submitted to approval inbox id={item.id})", file=sys.stderr)

    return True


def _handle_subagent_proposal(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Handle :subagent-proposal command — generate an autonomous subagent proposal."""
    tokens = _split_meta_args(rest)
    submit = False
    goal_parts: list[str] = []

    for token in tokens:
        if token == "--submit":
            submit = True
        else:
            goal_parts.append(token)

    goal = " ".join(goal_parts).strip()
    if not goal:
        print("Usage: :subagent-proposal <goal> [--submit]", file=sys.stderr)
        return True

    hint = "(needs delegation)" if needs_delegation(goal) else "(may not need delegation)"
    print(f"(subagent-proposal goal={goal!r} {hint})", file=sys.stderr)

    try:
        result = propose_subagent(
            goal,
            llm=agent.model_router.for_task(ModelRole.PLANNER, goal),
            logger=agent.logger if hasattr(agent, "logger") else None,
        )
    except Exception as exc:
        print(f"(subagent-proposal failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True

    print(result.user_summary(), file=sys.stderr)

    if result.ok and result.proposal is not None and submit:
        inbox = _approval_inbox_for(agent, workspace)
        item = inbox.add(
            operation="launch_subagent",
            summary=f"Subagent proposal: {result.proposal.proposed_role} — {goal}",
            risk="reversible",
            reasons=(f"why: {result.proposal.why_needed}",),
            payload=approval_payload_from_proposal(result.proposal),
        )
        print(f"(subagent-proposal submitted to approval inbox id={item.id})", file=sys.stderr)

    return True


def _handle_auto_status(agent: AgentLoop, workspace: Path) -> bool:
    status = AutonomousRuntime(
        agent,
        workspace=workspace,
        approval_inbox=_approval_inbox_for(agent, workspace),
    ).status()
    status["task_queue"] = _task_queue_for(agent, workspace).summary()
    status["scheduler"] = _scheduler_for(agent, workspace).summary()
    status["model_usage"] = agent.model_router.usage_snapshot()
    status["persistent_budget_windows"] = _budget_ledger_snapshot(agent)
    print(json.dumps(status, ensure_ascii=False, indent=2), file=sys.stderr)
    return True


# "Soft" status/capability intents that conversational phrasing can trip. For
# these, the keyword match is VERIFIED by the model (it tells a request apart
# from a passing mention) before dispatch. Explicit imperative intents are not
# gated.
_VERIFY_INTENTS: frozenset[str] = frozenset({
    "capability_check", "project_health", "smart_memory_status",
    "current_gaps_check", "weakness_finder", "next_safe_test",
    "best_next_action", "next_actions", "autonomy_readiness",
    "model_status", "budget_status", "approval_status", "urgent_status",
})


def _model_says_conversation(text: str, intent: OperatorIntent, agent: AgentLoop) -> bool:
    """True only when the model gives a CLEAR, parseable "this is conversation"
    verdict for a soft keyword match.

    On a confirmed action, or any uncertainty (model error / unparseable /
    ungrounded / low confidence / no model), returns False — so deterministic
    routing is preserved and the model only ever *vetoes* an obvious
    conversational false-positive (model advises, kernel decides).
    """
    try:
        llm = agent.model_router.for_role(ModelRole.PLANNER)
    except Exception:  # noqa: BLE001 — no model to consult -> keep routing
        return False
    decision = understand_intent(text, available_actions=(intent.kind,), llm=llm)
    return decision.kind == "conversation" and decision.source == "model"


def handle_conversational_operator_input(text: str, agent: AgentLoop, workspace: Path) -> bool:
    strategy = classify_operator_strategy(text)
    agent.log.log(
        "strategy_classified",
        {"strategy": strategy.value, "text_preview": text[:120]},
    )
    intent = route_operator_intent(text)
    if intent is None:
        return False
    # Bridge: for a soft match, let the model VETO an obvious conversational
    # false-positive. It only suppresses on a clear "this is conversation"
    # verdict; on uncertainty the deterministic route is preserved.
    if intent.kind in _VERIFY_INTENTS and _model_says_conversation(text, intent, agent):
        agent.log.log(
            "operator_intent_suppressed",
            {"kind": intent.kind, "reason": "model judged conversation, not a request"},
        )
        return False
    agent.log.log("operator_intent", intent.to_dict())
    if intent.kind == "shell_command_hint":
        print(
            "This looks like a shell/PowerShell command. "
            "Run it in PowerShell, not inside the agent REPL.",
            file=sys.stderr,
        )
        return True
    print(
        f"(operator intent: {intent.kind}; internal={intent.command})",
        file=sys.stderr,
    )
    return _dispatch_operator_intent(intent, agent, workspace, original_text=text)


def _dispatch_operator_intent(
    intent: OperatorIntent,
    agent: AgentLoop,
    workspace: Path,
    *,
    original_text: str = "",
) -> bool:
    if intent.kind == "capability_request":
        return _handle_capability_request(original_text, agent, workspace)
    if intent.kind == "self_build_request":
        return _handle_self_build_produce("", agent, workspace)
    if intent.kind == "subagent_proposal":
        return _handle_subagent_proposal(original_text.strip(), agent, workspace)
    if intent.kind == "architecture_audit":
        return _handle_architecture_audit("", agent, workspace)
    if intent.kind == "self_task_proposal":
        return _handle_self_task_propose("", agent, workspace)
    if intent.kind == "safe_self_check":
        return _handle_operator_check("", agent, workspace)
    if intent.kind == "capability_check":
        return _handle_operator_capability_check(agent, workspace)
    if intent.kind == "programming_readiness":
        return _handle_programming_readiness("", agent, workspace)
    if intent.kind == "current_gaps_check":
        return _handle_operator_gaps_check(agent, workspace)
    if intent.kind == "weakness_finder":
        return _handle_operator_weakness_finder(agent, workspace)
    if intent.kind == "next_safe_test":
        return _handle_next_safe_test(agent, workspace)
    if intent.kind == "project_health":
        return _handle_operator_check("", agent, workspace)
    if intent.kind == "smart_memory_status":
        return _handle_smart_memory("", agent)
    if intent.kind == "model_status":
        return _handle_models("", agent)
    if intent.kind == "budget_status":
        return _handle_operator_budget("", agent, workspace)
    if intent.kind == "approval_status":
        return _handle_approval_list("all", agent, workspace)
    if intent.kind == "urgent_status":
        return _handle_urgent_status("", agent, workspace)
    if intent.kind == "best_next_action":
        return _handle_best_next_action("", agent, workspace)
    if intent.kind == "next_actions":
        return _handle_next_actions("", agent, workspace)
    if intent.kind == "autonomy_readiness":
        return _handle_autonomy_readiness("", agent, workspace)
    if intent.kind == "source_review_plan":
        return _handle_source_review_plan(original_text, agent, workspace)
    if intent.kind == "implementation_plan":
        return _handle_implementation_plan(original_text, agent, workspace)
    if intent.kind == "patch_proposal":
        return _handle_patch_proposal_plan(original_text, agent, workspace)
    return False


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


def _handle_assumptions(rest: str, agent: AgentLoop) -> bool:  # Layer 5
    """Show the most-recent assumptions logged by the Assumption Registry."""
    use_json = "--json" in rest
    store = getattr(agent, "assumption_store", None)
    if store is None:
        print("(assumption store not enabled in this session)", file=sys.stderr)
        return True
    try:
        recent = store.load_recent(20)
    except Exception as exc:
        print(f"(assumption store error: {exc})", file=sys.stderr)
        return True
    if not recent:
        print("(no assumptions recorded yet)", file=sys.stderr)
        return True
    if use_json:
        print(json.dumps([a.to_dict() for a in recent], ensure_ascii=False, indent=2))
        return True
    current_run = getattr(getattr(agent, "log", None), "trace_id", None)
    for a in recent:
        run_tag = " [current]" if a.run_id == current_run else f" [run …{a.run_id[-8:]}]"
        verified_tag = " ✓" if a.verified is True else (" ✗" if a.verified is False else "")
        conf = int(a.confidence * 100)
        print(
            f"  [{a.category}] {a.text} ({conf}%){verified_tag}{run_tag}",
            file=sys.stderr,
        )
    return True


def handle_meta_command(cmd: str, agent: AgentLoop, workspace: Path) -> bool:
    """Returns True if the command was handled (so the REPL should skip the LLM)."""
    head, _, rest = cmd.partition(" ")
    head = head.lower()

    if head in {":mem", ":memory"}:
        if agent.memory is None:
            print("(no working memory in this session)", file=sys.stderr)
        else:
            print(json.dumps(agent.memory.summary(), ensure_ascii=False, indent=2))
        _print_persistent(agent)
        return True

    if head in {":smart-memory", ":memory-status"}:
        return _handle_smart_memory(rest.strip(), agent)

    if head == ":memory-consolidate":
        return _handle_memory_consolidate(rest.strip(), agent)

    if head == ":audit":
        arg = rest.strip().lower()
        if arg in {"on", "enable", "start"}:
            agent.set_audit_read_only(True)
            print(
                "(audit read-only ON — no durable memory writes this session; "
                "agent-auto persistent/semantic writes frozen; :remember still works)",
                file=sys.stderr,
            )
        elif arg in {"off", "disable", "stop"}:
            agent.set_audit_read_only(False)
            print(
                "(audit read-only OFF — durable memory writes resumed)",
                file=sys.stderr,
            )
        elif arg in {"", "status"}:
            state = "ON" if getattr(agent, "audit_read_only", False) else "OFF"
            print(f"(audit read-only: {state})", file=sys.stderr)
        else:
            print("Usage: :audit [on|off|status]", file=sys.stderr)
        return True

    if head in {":clear", ":reset"}:
        if agent.memory is None:
            print("(no working memory to clear)", file=sys.stderr)
        else:
            agent.memory.clear()
            agent.log.log("memory_clear", {"session_id": agent.memory.session_id})
            print("(working memory cleared — persistent memory untouched)", file=sys.stderr)
        return True

    if head == ":remember":
        tags, content = _parse_remember(rest)
        if not content:
            print(
                "Usage: :remember [tag1,tag2] <text>\n"
                "Examples:\n"
                "  :remember I prefer Python over JavaScript\n"
                "  :remember preference,fact I prefer concise Russian answers",
                file=sys.stderr,
            )
            return True
        decision, record = agent.remember(content=content, tags=tags, source="user-explicit")
        if decision.decision == "save" and record is not None:
            print(f"(saved as {record.id}; reasons: {'; '.join(decision.reasons)})", file=sys.stderr)
        else:
            print(f"(rejected: {'; '.join(decision.reasons)})", file=sys.stderr)
        return True

    if head == ":forget":
        target = rest.strip()
        if not target or target.lower() == "all":
            n = agent.forget(record_id=None)
            print(f"(deleted {n} persistent records)", file=sys.stderr)
        else:
            n = agent.forget(record_id=target)
            if n:
                print(f"(deleted {target})", file=sys.stderr)
            else:
                print(f"(no record with id {target})", file=sys.stderr)
        return True

    if head == ":ingest-source":
        return _handle_ingest_source(rest.strip(), agent, workspace)

    if head == ":ingest-project":
        return _handle_ingest_project(rest.strip(), agent, workspace)

    if head == ":source-library":
        return _handle_source_library(rest.strip())

    if head in {":source-registry", ":source-status"}:
        return _handle_source_registry(rest.strip(), agent, workspace)

    if head == ":source-review-plan":
        return _handle_source_review_plan(rest.strip(), agent, workspace)

    if head == ":implementation-plan":
        return _handle_implementation_plan(rest.strip(), agent, workspace)

    if head in {":patch-proposal-plan", ":patch-plan"}:
        return _handle_patch_proposal_plan(rest.strip(), agent, workspace)

    if head == ":self-build-propose":
        return _handle_self_build_propose(rest.strip(), agent, workspace)

    if head == ":self-build-supervisor":
        return _handle_self_build_supervisor(rest.strip(), agent, workspace)

    if head == ":ingest-web":
        return _handle_ingest_web(rest.strip(), agent, workspace)

    if head == ":ingest-rss":
        return _handle_ingest_rss(rest.strip(), agent, workspace)

    if head == ":connectors":
        return _handle_connectors(rest.strip())

    if head == ":connector-plan":
        return _handle_connector_plan(rest.strip())

    if head in {":models", ":model-routes"}:
        return _handle_models(rest.strip(), agent)

    if head in {":model-registry-audit", ":model-audit"}:
        return _handle_model_registry_audit(rest.strip(), agent)

    if head in {":refresh-models", ":model-catalog-refresh", ":model-refresh"}:
        return _handle_refresh_models(rest.strip(), agent)

    if head in {":model-discovery-audit", ":discovery-audit"}:
        return _handle_model_discovery_audit(rest.strip(), agent)

    if head in {":provider-catalog-refresh"}:
        return _handle_provider_catalog_refresh(rest.strip(), agent)

    if head in {":architecture-audit", ":arch-audit", ":roadmap-audit"}:
        return _handle_architecture_audit(rest.strip(), agent, workspace)

    if head in {":operator-check", ":project-check", ":project-status"}:
        return _handle_operator_check(rest.strip(), agent, workspace)

    if head in {":operator-budget", ":budget-digest"}:
        return _handle_operator_budget(rest.strip(), agent, workspace)

    if head in {":budget-config", ":budget-limits"}:
        return _handle_budget_config(rest.strip(), agent, workspace)

    if head in {":urgent-status", ":operator-urgent"}:
        return _handle_urgent_status(rest.strip(), agent, workspace)

    if head in {":next-actions", ":operator-next"}:
        return _handle_next_actions(rest.strip(), agent, workspace)

    if head in {":autonomy-readiness", ":operator-readiness"}:
        return _handle_autonomy_readiness(rest.strip(), agent, workspace)

    if head == ":dry-health-pass":
        return _handle_dry_health_pass(rest.strip(), agent, workspace)

    if head in {":coding-readiness", ":programming-readiness"}:
        return _handle_programming_readiness(rest.strip(), agent, workspace)

    if head == ":operator-task":
        return _handle_operator_task(rest, agent, workspace)

    if head in {":learn", ":learn-project"}:
        return _handle_learn(rest.strip(), agent, workspace)

    if head == ":auto-run":
        return _handle_auto_run(rest.strip(), agent, workspace)

    if head in {":work-session", ":work-sess"}:
        return _handle_work_session(rest.strip(), agent, workspace)

    if head in {":campaign-start", ":campaign"}:
        return _handle_campaign_start(rest.strip(), agent, workspace)

    if head in {":campaign-status", ":campaign-ledger"}:
        return _handle_campaign_status(rest.strip(), agent, workspace)

    if head in {":capability-request", ":capability-proposal"}:
        return _handle_capability_request(rest.strip(), agent, workspace)

    if head == ":auto-status":
        return _handle_auto_status(agent, workspace)

    if head in {":conflicts", ":conflict-status"}:
        return _handle_conflicts(rest.strip(), agent, workspace)

    if head == ":budget-status":
        return _handle_budget_status(agent, workspace)

    if head in {":budget-window-status", ":budget-windows", ":budget-ledger"}:
        return _handle_budget_window_status(rest.strip(), agent)

    if head in {":budget-kill-switch", ":budget-killswitch", ":kill-switch"}:
        return _handle_budget_kill_switch(rest.strip(), agent, workspace)

    if head in {":state-store-drill", ":state-drill", ":state-recovery-drill"}:
        return _handle_state_store_drill(rest.strip(), agent, workspace)

    if head in {":release-audit", ":release-hygiene"}:
        return _handle_release_audit(rest.strip(), agent, workspace)

    if head in {":supply-chain-audit", ":supply-audit", ":ci-audit"}:
        return _handle_supply_chain_audit(rest.strip(), agent, workspace)

    if head in {":model-usage", ":usage-models"}:
        return _handle_model_usage(rest.strip(), agent)

    if head in {":team-plan", ":agent-team", ":subagents"}:
        return _handle_team_plan(rest.strip(), agent)

    if head in {":subagent-proposal", ":propose-subagent"}:
        return _handle_subagent_proposal(rest.strip(), agent, workspace)

    if head in {":team-run", ":team-execute", ":subagents-run"}:
        return _handle_team_run(rest.strip(), agent, workspace)

    if head == ":approval-list":
        return _handle_approval_list(rest.strip(), agent, workspace)

    if head in {":approval-triage", ":triage"}:
        return _handle_approval_triage(rest.strip(), agent, workspace)

    if head in {":best-next-action", ":next-action", ":bna"}:
        return _handle_best_next_action(rest.strip(), agent, workspace)

    if head == ":self-issue-verify":
        return _handle_self_issue_verify(rest.strip(), agent, workspace)

    if head in {":ack", ":acknowledge"}:
        return _handle_alert_ack(rest.strip(), agent, workspace)

    if head in {":ack-list", ":acks"}:
        return _handle_alert_ack_list(rest.strip(), agent, workspace)

    if head in {":ack-clear", ":unack"}:
        return _handle_alert_ack_clear(rest.strip(), agent, workspace)

    # Short aliases for approval commands
    if head == ":inbox":
        return _handle_approval_list("pending", agent, workspace)

    if head == ":approve":
        return _handle_approval_decision(rest.strip(), agent, workspace, decision="approve")

    if head == ":deny":
        return _handle_approval_decision(rest.strip(), agent, workspace, decision="deny")

    if head == ":approval-approve":
        return _handle_approval_decision(rest.strip(), agent, workspace, decision="approve")

    if head == ":approval-deny":
        return _handle_approval_decision(rest.strip(), agent, workspace, decision="deny")

    if head == ":approval-run":
        return _handle_approval_run(rest.strip(), agent, workspace)

    if head == ":self-apply-run":
        return _handle_self_apply_run(rest.strip(), agent, workspace)

    if head == ":self-build-produce":
        return _handle_self_build_produce(rest.strip(), agent, workspace)

    if head == ":self-split":
        return _handle_self_split(rest.strip(), agent, workspace)

    if head == ":self-task-propose":
        return _handle_self_task_propose(rest.strip(), agent, workspace)

    if head == ":self-task-build":
        return _handle_self_task_build(rest.strip(), agent, workspace)

    if head == ":value-review":
        return _handle_value_review(rest.strip(), agent, workspace)

    if head == ":value-review-list":
        return _handle_value_review_list(rest.strip(), agent, workspace)

    if head == ":approval-abort":
        return _handle_approval_abort(rest.strip(), agent, workspace)

    if head == ":queue-status":
        return _handle_queue_status(agent, workspace)

    if head == ":scheduler-status":
        return _handle_scheduler_status(agent, workspace)

    if head == ":task-add":
        return _handle_task_add(rest.strip(), agent, workspace)

    if head == ":task-list":
        return _handle_task_list(rest.strip(), agent, workspace)

    if head == ":task-run":
        return _handle_task_run(rest.strip(), agent, workspace)

    if head == ":task-cancel":
        return _handle_task_cancel(rest.strip(), agent, workspace)

    if head == ":schedule-add":
        return _handle_schedule_add(rest.strip(), agent, workspace)

    if head == ":schedule-list":
        return _handle_schedule_list(rest.strip(), agent, workspace)

    if head == ":schedule-disable":
        return _handle_schedule_disable(rest.strip(), agent, workspace)

    if head == ":schedule-tick":
        return _handle_schedule_tick(rest.strip(), agent, workspace)

    if head == ":hygiene":
        return _handle_hygiene(rest.strip(), agent, workspace)

    if head == ":rollback":
        return _handle_rollback(rest.strip(), agent, workspace)

    if head == ":repair":
        return _handle_repair(rest.strip(), agent, workspace)

    if head == ":propose-repair":
        return _handle_propose_repair(rest.strip(), agent, workspace)

    if head in {":help", "?"}:
        print(render_help(), file=sys.stderr)
        return True

    if head in {":quit", ":exit"}:
        raise SystemExit(0)

    if head in {":assumptions", ":assumption-log"}:  # Layer 5
        return _handle_assumptions(rest.strip(), agent)

    return False


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
