"""``:self-build-produce`` REPL command (TD-025, grounded default TD-036).

A single narrow operator trigger that runs the subagent-backed self-apply
*producer*: a Manager/Researcher/Builder/Critic/Reporter pipeline that generates
at most one validated low-risk full-content proposal and drops it into the
approval inbox as an ``operation="self_apply_lane.run"`` item.

The Manager picks its target + diagnosis from a *grounded* backlog candidate
(TECH_DEBT.md / docs/AGENT_ANATOMY.md) by default — it never invents a diagnosis
via the LLM. When the grounded path yields no publishable target (empty backlog,
or a candidate that is off-allowlist / critical) the command returns ``no_patch``
with the precise grounded reason instead of falling back to the LLM.

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

    # Surface the grounded Manager decision (TD-036) so the operator can see
    # whether a verifiable backlog candidate drove the run, and — when it did not
    # produce a patch — the precise grounded reason (empty backlog, off-allowlist
    # target, or critical target) rather than a vague message.
    manager = next(
        (r for r in (result.get("roles") or []) if r.get("role") == "manager"),
        None,
    )
    manager_data = (manager or {}).get("data", {})
    grounded = bool(manager_data.get("grounded"))
    evidence_ref = manager_data.get("evidence_ref") or ""
    manager_detail = (manager or {}).get("detail") or ""
    # True when the grounded path ran but yielded no publishable target. This is
    # NOT necessarily an empty backlog — a candidate may have been found and then
    # rejected as off-allowlist/critical; manager_detail carries the exact reason.
    no_grounded_target = (
        result.get("status") == "no_patch"
        and manager is not None
        and manager.get("decision") == "no_target"
        and not grounded
    )

    # Secret-free structured log (never dumps generated file content).
    agent.log.log(
        "self_build_produce",
        {
            "status": result.get("status"),
            "target_path": result.get("target_path"),
            "approval_id": result.get("approval_id"),
            "checked_gates": result.get("checked_gates"),
            "veto_reasons": result.get("veto_reasons"),
            "grounded": grounded,
            "evidence_ref": evidence_ref,
            "no_grounded_target": no_grounded_target,
        },
    )

    lines = [
        "=== self-build produce ===",
        f"status: {result.get('status')}",
        f"reason: {result.get('reason')}",
    ]
    if grounded:
        lines.append("manager: grounded backlog candidate")
        if evidence_ref:
            lines.append(f"evidence_ref: {evidence_ref}")
    elif no_grounded_target:
        detail = manager_detail or "no verifiable grounded candidate"
        lines.append(f"manager: no grounded target ({detail})")
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
