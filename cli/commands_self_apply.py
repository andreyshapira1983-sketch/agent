"""``:self-apply-run`` REPL command (TD-024).

The single narrow operator trigger that routes one *already approved* inbox
item through the trusted self-apply lane. It accepts exactly one approval id
and nothing else — no free-text patch, no extra arguments. All the policy lives
in :mod:`core.self_apply_bridge`; this module only wires runtime dependencies
(inbox, SafeVCS, test runner, kill-switch, budget snapshot) and prints a report.

This command is deliberately not wired into any daemon / scheduler / agent_tick
path: a human runs it explicitly, one proposal at a time.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from core.budget_kill_switch import BudgetKillSwitch, default_path
from core.safe_vcs import SafeVCS
from core.self_apply_bridge import run_approved_self_apply
from tools.run_tests import RunTestsTool

from cli.commands_approval import _approval_inbox_for
from cli.commands_budget import _budget_ledger_snapshot

if TYPE_CHECKING:
    from core.loop import AgentLoop


def _handle_self_apply_run(rest: str, agent: "AgentLoop", workspace: Path) -> bool:
    # Exactly one bare approval id; reject free text / extra args / patch bodies.
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
    except Exception:
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

    if "--json" in parts:  # never true (len==1 guard) but keep symmetry cheap
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return True

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
    lines.append(f"next: {result.get('next_human_action')}")
    print("\n".join(lines), file=sys.stderr)
    return True
