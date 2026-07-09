"""``:self-task-propose`` REPL command (roadmap Ступень 1, Stage A).

A single narrow operator trigger that runs the Stage-A *task producer*: from a
real ``# TODO``/``# FIXME`` comment in the codebase it generates one coding task
plus a failing acceptance test and drops a single approval item
(``operation="self_build_task.approve"``) into the approval inbox.

It NEVER writes implementation code and NEVER applies anything. The human reads
the proposed test, approves the task with ``:approval-approve``, then triggers
the implementation with ``:self-task-build`` (Stage B). Both applied steps stay
behind human-in-the-loop approval and the existing self-apply lane.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from core.budget_kill_switch import BudgetKillSwitch, default_path
from core.safe_vcs import SafeVCS
from core.self_task_builder import build_coding_task
from core.self_task_producer import decode_frozen_test, produce_coding_task

from cli.commands_approval import _approval_inbox_for
from cli.commands_budget import _budget_ledger_snapshot
from cli.self_build_memory import record_self_build_episode

if TYPE_CHECKING:
    from core.loop import AgentLoop


def _handle_self_task_propose(rest: str, agent: "AgentLoop", workspace: Path) -> bool:
    # Narrow trigger: no arguments. Anything extra is a misuse.
    if rest.strip():
        print(
            "Usage: :self-task-propose\n"
            "  (no arguments; proposes at most one coding-task approval item)",
            file=sys.stderr,
        )
        return True

    snapshot = _budget_ledger_snapshot(agent)
    kill_state = BudgetKillSwitch(path=default_path(workspace)).status(snapshot)
    inbox = _approval_inbox_for(agent, workspace)

    report = produce_coding_task(
        workspace=workspace,
        inbox=inbox,
        llm=agent.model_router.for_role("synthesizer"),
        vcs=SafeVCS(workspace=workspace),
        budget_snapshot=snapshot,
        kill_switch=kill_state,
    )
    result = report.to_dict()

    # Secret-free structured log (never dumps generated test content).
    agent.log.log(
        "self_task_produce",
        {
            "status": result.get("status"),
            "target_path": result.get("target_path"),
            "approval_id": result.get("approval_id"),
            "checked_gates": result.get("checked_gates"),
            "veto_reasons": result.get("veto_reasons"),
        },
    )

    # Journal the attempt into episodic memory so the agent accumulates lessons.
    record_self_build_episode(agent, kind="self-task-produce", result=result)

    lines = [
        "=== self-task propose ===",
        f"status: {result.get('status')}",
        f"reason: {result.get('reason')}",
    ]
    if result.get("target_path"):
        lines.append(f"impl: {result.get('target_path')}")
    if result.get("approval_id"):
        lines.append(f"approval_id: {result.get('approval_id')}")
    if result.get("veto_reasons"):
        lines.append(f"veto_reasons: {result.get('veto_reasons')}")
    roles = result.get("roles") or []
    if roles:
        lines.append(
            "roles: " + ", ".join(f"{r['role']}:{r['decision']}" for r in roles)
        )
    lines.append(f"next: {result.get('next_human_action')}")
    print("\n".join(lines), file=sys.stderr)

    # Show the FULL proposed acceptance test so the human can read the yardstick
    # BEFORE approving it (the Stage-A anti-cheating guarantee). We decode the
    # exact, redaction-inert copy so example PII in fixtures is shown verbatim.
    approval_id = result.get("approval_id")
    if approval_id:
        item = next((i for i in inbox.list() if i.id == approval_id), None)
        if item is not None:
            frozen = decode_frozen_test(item.payload)
            if frozen.strip():
                print(
                    "\n--- proposed acceptance test (READ before approving) ---\n"
                    f"# {item.payload.get('test_path', '')}\n{frozen}"
                    "--- end of test ---",
                    file=sys.stderr,
                )
    return True


def _handle_self_task_build(rest: str, agent: "AgentLoop", workspace: Path) -> bool:
    # Requires exactly one argument: the approved coding-task approval id.
    approval_id = rest.strip()
    if not approval_id or len(approval_id.split()) != 1:
        print(
            "Usage: :self-task-build <approval_id>\n"
            "  (the id of an APPROVED coding task from :self-task-propose)",
            file=sys.stderr,
        )
        return True

    snapshot = _budget_ledger_snapshot(agent)
    kill_state = BudgetKillSwitch(path=default_path(workspace)).status(snapshot)
    inbox = _approval_inbox_for(agent, workspace)

    report = build_coding_task(
        workspace=workspace,
        inbox=inbox,
        approval_id=approval_id,
        llm=agent.model_router.for_role("synthesizer"),
        vcs=SafeVCS(workspace=workspace),
        budget_snapshot=snapshot,
        kill_switch=kill_state,
    )
    result = report.to_dict()

    # Secret-free structured log (never dumps generated implementation content).
    agent.log.log(
        "self_task_build",
        {
            "status": result.get("status"),
            "target_path": result.get("target_path"),
            "approval_id": result.get("approval_id"),
            "checked_gates": result.get("checked_gates"),
            "veto_reasons": result.get("veto_reasons"),
        },
    )

    # Journal the attempt into episodic memory so the agent accumulates lessons.
    record_self_build_episode(agent, kind="self-task-build", result=result)

    lines = [
        "=== self-task build ===",
        f"status: {result.get('status')}",
        f"reason: {result.get('reason')}",
    ]
    if result.get("target_path"):
        lines.append(f"impl: {result.get('target_path')}")
    if result.get("approval_id"):
        lines.append(f"approval_id: {result.get('approval_id')}")
    if result.get("veto_reasons"):
        lines.append(f"veto_reasons: {result.get('veto_reasons')}")
    roles = result.get("roles") or []
    if roles:
        lines.append(
            "roles: " + ", ".join(f"{r['role']}:{r['decision']}" for r in roles)
        )
    lines.append(f"next: {result.get('next_human_action')}")
    print("\n".join(lines), file=sys.stderr)
    return True

