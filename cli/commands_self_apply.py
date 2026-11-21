"""``:self-apply-run``: apply one already approved inbox item through the self-apply lane."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from cli.commands_approval import _approval_inbox_for
from cli.commands_budget import _budget_ledger_snapshot
from cli.self_build_memory import record_self_build_episode
from core.budget_kill_switch import BudgetKillSwitch, default_path
from core.safe_vcs import SafeVCS
from core.self_apply_bridge import run_approved_self_apply
from tools.run_tests import RunTestsTool

if TYPE_CHECKING:
    from core.loop import AgentLoop


def _handle_self_apply_run(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    parts = rest.split()
    if len(parts) != 1:
        print(
            "Usage: :self-apply-run <approval-inbox-id>\n"
            "  (exactly one approved inbox id; no patch text or extra args)",
            file=sys.stderr,
        )
        return True
    item_id = parts[0]

    inbox = _approval_inbox_for(agent, workspace)
    try:
        from core.subagent_registry import SubagentRegistry
        registry = SubagentRegistry.load(workspace)
    except Exception:  # noqa: BLE001 — best-effort side hook; any failure means no registry
        registry = None
    result = run_approved_self_apply(
        inbox=inbox,
        item_id=item_id,
        workspace=workspace,
        vcs=SafeVCS(workspace=workspace),
        test_runner=RunTestsTool(workspace_root=workspace),
        kill_switch=BudgetKillSwitch(path=default_path(workspace)),
        budget_snapshot=_budget_ledger_snapshot(agent),
        registry=registry,
    )

    # Secret-free structured log (never dumps file content or diffs).
    agent.log.log(
        "self_apply_run",
        {
            "proposal_id": result.get("proposal_id"),
            "status": result.get("status"),
            "branch": result.get("branch"),
            "files_changed": result.get("files_changed"),
            "rollback_status": result.get("rollback_status"),
            "commit_hash": result.get("commit_hash"),
            "origin": result.get("origin"),
        },
    )

    record_self_build_episode(agent, kind="self-apply-run", result=result)

    # A rollback's machine-readable cause becomes a hard rule the Critic enforces later,
    # plus a lesson with provenance in the same store the autonomous path uses.
    try:
        from core.self_build_rules import record_lessons_from_result

        added = record_lessons_from_result(
            workspace, result, origin="cli:self-apply-run"
        )
        if added.get("rules") or added.get("lessons"):
            agent.log.log(
                "self_build_rules_recorded",
                {
                    "proposal_id": result.get("proposal_id"),
                    "rules_added": added.get("rules", 0),
                    "lessons_added": added.get("lessons", 0),
                },
            )
    except Exception:  # noqa: BLE001, S110 — rule recording must never break the command
        pass

    # No --json mode on purpose: with one argument allowed, --json could only arrive as the id.
    lines = [
        "=== self-apply run ===",
        f"proposal: {result.get('proposal_id')}",
        f"status: {result.get('status')}",
        f"reason: {result.get('reason')}",
    ]
    if result.get("branch"):
        lines.append(f"branch: {result.get('branch')}")
    if result.get("files_changed"):
        lines.append(f"files_changed: {result.get('files_changed')}")
    if result.get("rollback_status") and result.get("rollback_status") != "none":
        lines.append(f"rollback_status: {result.get('rollback_status')}")
    if result.get("commit_hash"):
        lines.append(f"commit: {result.get('commit_hash')}")
    if result.get("rejected_files"):
        lines.append(f"rejected_files: {result.get('rejected_files')}")
    # Print risks before "next": the human needs them for the merge decision.
    for risk in result.get("risks") or ():
        lines.append(f"risk: {risk}")
    lines.append(f"next: {result.get('next_human_action')}")
    print("\n".join(lines), file=sys.stderr)
    return True
