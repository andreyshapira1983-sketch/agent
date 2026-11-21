"""``--ask <question>``: one question in, one answer out, then exit (no persistent memory)."""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

# Collaborators are imported as modules so a test's monkeypatch on them is seen here.
from app import budget_guard
from app.bootstrap import build_agent as _build_agent
from cli import command_dispatch, intent_bridge
from core.approval import ApprovalProvider, AutoApprover
from core.loop import format_human_response

if TYPE_CHECKING:  # annotations only
    from collections.abc import Callable
    from pathlib import Path


def run_one_shot(
    ask: str,
    *,
    workspace: Path,
    file_hint: str | None = None,
    auto_approve: str = "off",
    reason: str | None = None,
    expect: str | None = None,
    # trace_id of a resumed budget pause; a completed resume retires its task.
    resumed_from: str | None = None,
    # cli/app.py passes its own binding, so a test patch there is observed here too.
    build_agent: Callable[..., object] = _build_agent,
    # Earlier (question, answer) turns from --history.
    history: list[tuple[str, str]] | None = None,
    # Long-term memory only when the flag is given.
    with_persistent: bool = False,
) -> int:
    """Run a single question end-to-end and return the process exit code."""
    # One-shot cannot prompt a human: only --auto-approve wires a provider.
    if auto_approve == "approve":
        approval_provider: ApprovalProvider | None = AutoApprover(default="approve")
    elif auto_approve == "deny":
        approval_provider = AutoApprover(default="deny")
    else:
        # 'off' in one-shot = no provider wired = escalated tools blocked.
        approval_provider = None

    # Session memory only with --history, persistent memory only by flag (the frozen
    # contract). Experience is a separate axis and always on; an unchanged repeat is
    # replayed from memory (gate: core/replay_freshness.py).
    agent = build_agent(
        workspace,
        with_memory=bool(history),
        with_persistent=with_persistent,
        with_experience=True,
        episodic_replay=True,
        approval_provider=approval_provider,
    )
    _load_history(agent, history)
    # ':' commands win over fuzzy intent routing, as in the REPL (the classifier misreads them).
    ask_head = ask.lstrip()
    if ask_head.startswith(":") or ask_head == "?":
        try:
            handled = command_dispatch.handle_meta_command(ask_head, agent, workspace)
        except Exception as exc:  # noqa: BLE001 — one line and a failing exit code, not a traceback
            label = ask_head.split(maxsplit=1)[0]
            print(f"(command failed: {label}: {type(exc).__name__}: {exc})", file=sys.stderr)
            return 1
        if handled:
            return 0
        print(f"(unknown command: {ask_head})", file=sys.stderr)
        return 0
    if intent_bridge._handle_local_operator_reply(ask, agent):
        return 0
    if intent_bridge.handle_conversational_operator_input(ask, agent, workspace):
        return 0
    # Deep tier only with --reason/--expect; otherwise deep requests downgrade.
    deep_escalation = None
    if reason or expect:
        from core.deep_escalation import OperatorEscalation
        deep_escalation = OperatorEscalation(
            reason=reason,
            expected_output=expect,
        )
    # stream=False: with streaming the answer would be printed twice.
    answer = budget_guard._run_agent_with_budget_guard(
        agent,
        user_question=ask,
        file_hint=file_hint,
        workspace=workspace,
        stream=False,
        deep_escalation=deep_escalation,
        resumed_from=resumed_from,
    )
    print("\n" + format_human_response(answer) + "\n")
    return 0


#: Max earlier turns loaded into session memory; WorkingMemory may still trim them.
HISTORY_TURNS = 8


def read_history(path: str | None) -> list[tuple[str, str]]:
    """Read (question, answer) pairs from a --history JSONL file; bad lines are skipped."""
    import json
    from pathlib import Path

    if not path:
        return []
    pairs: list[tuple[str, str]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        question, answer = str(row.get("question") or ""), str(row.get("answer") or "")
        if question.strip() and answer.strip():
            pairs.append((question, answer))
    return pairs[-HISTORY_TURNS:]


def _load_history(agent: object, history: list[tuple[str, str]] | None) -> None:
    memory = getattr(agent, "memory", None)
    if not history or memory is None:
        return
    for question, answer in history:
        memory.record_turn(question, "", [], [], answer)
