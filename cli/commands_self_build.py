"""``:self-build-produce`` REPL command (TD-025).

A single narrow operator trigger that runs the subagent-backed self-apply
*producer*: a Manager/Researcher/Builder/Critic/Reporter pipeline that generates
at most one validated low-risk full-content proposal and drops it into the
approval inbox as an ``operation="self_apply_lane.run"`` item.

It is deliberately narrow:

* it accepts NO arguments and NO free-text patch;
* it ONLY creates one approval inbox item — it never applies the patch, never
  runs the lane, never commits/pushes/merges;
* it is NOT wired into any daemon / scheduler / agent_tick path.

The applied step stays behind the existing human-in-the-loop ``:self-apply-run``
command (TD-024).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from core.budget_kill_switch import BudgetKillSwitch, default_path
from core.safe_vcs import SafeVCS
from core.self_build_producer import produce_self_apply_proposal

from cli.commands_approval import _approval_inbox_for
from cli.commands_budget import _budget_ledger_snapshot

if TYPE_CHECKING:
    from core.loop import AgentLoop


def _handle_self_build_produce(rest: str, agent: "AgentLoop", workspace: Path) -> bool:
    # Narrow trigger: no arguments, no free-text patch. Anything extra is a
    # misuse and is rejected outright.
    if rest.strip():
        print(
            "Usage: :self-build-produce\n"
            "  (no arguments; produces at most one self-apply approval item)",
            file=sys.stderr,
        )
        return True

    snapshot = _budget_ledger_snapshot(agent)
    kill_state = BudgetKillSwitch(path=default_path(workspace)).status(snapshot)
    inbox = _approval_inbox_for(agent, workspace)

    report = produce_self_apply_proposal(
        workspace=workspace,
        inbox=inbox,
        llm=agent.model_router.for_role("synthesizer"),
        vcs=SafeVCS(workspace=workspace),
        budget_snapshot=snapshot,
        kill_switch=kill_state,
    )
    result = report.to_dict()

    # Secret-free structured log (never dumps generated file content).
    agent.log.log(
        "self_build_produce",
        {
            "status": result.get("status"),
            "target_path": result.get("target_path"),
            "approval_id": result.get("approval_id"),
            "checked_gates": result.get("checked_gates"),
            "veto_reasons": result.get("veto_reasons"),
        },
    )

    lines = [
        "=== self-build produce ===",
        f"status: {result.get('status')}",
        f"reason: {result.get('reason')}",
    ]
    if result.get("target_path"):
        lines.append(f"target: {result.get('target_path')}")
    if result.get("approval_id"):
        lines.append(f"approval_id: {result.get('approval_id')}")
    if result.get("veto_reasons"):
        lines.append(f"veto_reasons: {result.get('veto_reasons')}")
    roles = result.get("roles") or []
    if roles:
        lines.append(
            "roles: "
            + ", ".join(f"{r['role']}:{r['decision']}" for r in roles)
        )
    lines.append(f"next: {result.get('next_human_action')}")
    print("\n".join(lines), file=sys.stderr)
    return True
