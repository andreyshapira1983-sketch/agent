"""Approval-inbox, alert-acknowledgement and best-next-action REPL commands.

Split out of ``main.py``. Every function here depends only on ``core`` classes
and on helpers within this module — never back into ``main`` — so there is no
import cycle. ``main.py`` re-exports the shared helper ``_approval_inbox_for``
(used by auto-run / work-session / campaign / operator-digest) and the command
handlers wired into the REPL dispatch.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.approval_inbox import ApprovalInbox, DEFAULT_APPROVAL_INBOX_PATH
from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig

if TYPE_CHECKING:
    from core.loop import AgentLoop


DEFAULT_ALERT_ACK_PATH = Path("data") / "alert_acknowledgements.jsonl"


def _approval_inbox_for(agent: AgentLoop, workspace: Path | None = None) -> ApprovalInbox:
    inbox = getattr(agent, "approval_inbox", None)
    if inbox is None:
        path = (workspace / DEFAULT_APPROVAL_INBOX_PATH) if workspace is not None else None
        inbox = ApprovalInbox(path=path)
        setattr(agent, "approval_inbox", inbox)
    return inbox


def _alert_ack_store_for(workspace: Path | None = None):
    """Build an :class:`AlertAckStore` bound to the workspace runtime state."""
    from core.alert_ack import AlertAckStore

    path = (workspace / DEFAULT_ALERT_ACK_PATH) if workspace is not None else None
    return AlertAckStore(path=path)


def _payload_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _handle_approval_list(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    status = rest.strip().lower() or "pending"
    items = _approval_inbox_for(agent, workspace).list(status=status)
    if not items:
        print(f"(no approvals: status={status})", file=sys.stderr)
        return True
    print(f"=== approval inbox ({len(items)}; status={status}) ===", file=sys.stderr)
    for item in items:
        print(
            f"  {item.id} [{item.status}] risk={item.risk} "
            f"operation={item.operation} summary={item.summary}",
            file=sys.stderr,
        )
        if item.reasons:
            print(f"    reasons={list(item.reasons)}", file=sys.stderr)
    return True


def _handle_approval_triage(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Read-only triage of the pending approval inbox.

    Groups pending proposed_task items into clusters, flags duplicates / stale
    / dangerous / low-value items, and prints a recommended_action per item.
    Never deletes or executes anything — purely advisory.
    """
    from core.approval_triage import format_triage_report, triage_inbox

    inbox = _approval_inbox_for(agent, workspace)
    report = triage_inbox(inbox.pending())
    print(format_triage_report(report), file=sys.stderr)
    agent.log.log("approval_inbox_triage", report.to_dict())
    return True


def _handle_best_next_action(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Choose and explain the single most important next action — advisory only.

    Gathers current signals (latest daemon heartbeat + a read-only inbox triage
    pass) and asks the pure priority-intelligence selector for ONE action, with
    evidence, risk, and an honest list of what the agent does not know. Nothing
    is executed.
    """
    import agent_tick
    from core.approval_triage import triage_inbox
    from core.best_next_action import (
        format_best_next_action,
        select_best_next_action,
    )

    heartbeat = agent_tick._read_heartbeat(workspace)
    age = agent_tick._heartbeat_age_seconds(heartbeat)
    hb = heartbeat or {}

    inbox = _approval_inbox_for(agent, workspace)
    triage = triage_inbox(inbox.pending())

    ack_store = _alert_ack_store_for(workspace)
    acknowledged = ack_store.active_actions()

    action = select_best_next_action(
        result_status=str(hb.get("result_status", "none")),
        tests_health=str(hb.get("tests_health", "none")),
        dry_run_streak=int(hb.get("dry_run_streak", 0) or 0),
        heartbeat_missing=heartbeat is None,
        heartbeat_stale=agent_tick._is_stale(age),
        heartbeat_age_seconds=age,
        last_event=str(hb.get("event", "")),
        tick_error=hb.get("error"),
        triage=triage,
        inbox_pending=triage.total_pending,
        acknowledged=acknowledged,
    )

    if rest.strip() == "--json":
        print(json.dumps(action.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(format_best_next_action(action), file=sys.stderr)
        if acknowledged:
            print(
                f"  (acknowledged alert(s) currently suppressed: {', '.join(sorted(acknowledged))} "
                "— :ack-list to review, :ack-clear <action> to restore)",
                file=sys.stderr,
            )
    agent.log.log("best_next_action", action.to_dict())
    return True


def _handle_alert_ack(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Acknowledge an advisory alert so it stops dominating :best-next-action.

    Usage: ``:ack <action> [--ttl <hours>] [reason words...]``. Only advisory
    (medium/low) alerts can be acknowledged — objective breakages are rejected.
    Read/write of runtime state only; never executes the alert's action.
    """
    from core.best_next_action import is_suppressible_alert

    tokens = rest.split()
    if not tokens:
        print(
            "Usage: :ack <action> [--ttl <hours>] [reason...]\n"
            "  (advisory alerts only, e.g. review_dry_run_stall, "
            "reduce_inbox_duplicate_debt, review_inbox_backlog)",
            file=sys.stderr,
        )
        return True

    action = tokens[0]
    if not is_suppressible_alert(action):
        print(
            f"(ack refused: '{action}' is not an acknowledgeable advisory alert — "
            "objective breakages (daemon/tests/tick errors) can never be suppressed)",
            file=sys.stderr,
        )
        return True

    ttl_hours: float | None = None
    reason_parts: list[str] = []
    i = 1
    while i < len(tokens):
        if tokens[i] == "--ttl" and i + 1 < len(tokens):
            try:
                ttl_hours = float(tokens[i + 1])
            except ValueError:
                print(f"(ack: invalid --ttl value '{tokens[i + 1]}', ignoring)", file=sys.stderr)
            i += 2
            continue
        reason_parts.append(tokens[i])
        i += 1

    store = _alert_ack_store_for(workspace)
    ack = store.acknowledge(
        action=action,
        acknowledged_by="operator",
        reason=" ".join(reason_parts),
        ttl_hours=ttl_hours,
    )
    ttl_note = f" (expires {ack.expires_at})" if ack.expires_at else " (no expiry)"
    print(
        f"acknowledged: {action}{ttl_note}\n"
        f"  reason: {ack.reason or '(none given)'}\n"
        "  note: the alert is suppressed from the top pick but still computed and "
        "reported; use :ack-clear to restore it.",
        file=sys.stderr,
    )
    agent.log.log("alert_acknowledged", ack.to_dict())
    return True


def _handle_alert_ack_list(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """List active operator acknowledgements. Read-only."""
    store = _alert_ack_store_for(workspace)
    active = store.list_active()
    if not active:
        print("no active acknowledgements.", file=sys.stderr)
        return True
    print(f"active acknowledgement(s): {len(active)}", file=sys.stderr)
    for ack in active:
        ttl = f"expires {ack.expires_at}" if ack.expires_at else "no expiry"
        print(
            f"  - {ack.action}  [{ttl}]  by={ack.acknowledged_by}  "
            f"reason={ack.reason or '(none)'}",
            file=sys.stderr,
        )
    print("  note: :ack-clear <action> to restore an alert to the top-pick race.", file=sys.stderr)
    return True


def _handle_alert_ack_clear(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Un-acknowledge an alert so it can dominate :best-next-action again."""
    action = rest.strip()
    if not action:
        print("Usage: :ack-clear <action>", file=sys.stderr)
        return True
    store = _alert_ack_store_for(workspace)
    removed = store.clear(action)
    if removed:
        print(f"cleared acknowledgement for: {action} (restored to top-pick race)", file=sys.stderr)
        agent.log.log("alert_ack_cleared", {"action": action, "removed": removed})
    else:
        print(f"(no active acknowledgement found for '{action}')", file=sys.stderr)
    return True


def _handle_approval_decision(
    rest: str,
    agent: AgentLoop,
    workspace: Path,
    *,
    decision: str,
) -> bool:
    item_id = rest.strip()
    if not item_id:
        print(f"Usage: :approval-{decision} <approval_id>", file=sys.stderr)
        return True
    inbox = _approval_inbox_for(agent, workspace)
    try:
        if decision == "approve":
            item = inbox.approve(item_id)
        elif decision == "deny":
            item = inbox.deny(item_id)
        else:
            raise ValueError(f"unknown approval decision: {decision}")
    except KeyError as exc:
        print(f"(approval {decision} failed: {exc})", file=sys.stderr)
        return True
    agent.log.log("approval_inbox_decision", item.to_dict())
    if decision == "approve":
        _record_producer_approval(workspace, item)
    past = "approved" if decision == "approve" else "denied"
    print(f"(approval {past}: {item.id}; operation={item.operation})", file=sys.stderr)
    return True


def _record_producer_approval(workspace: Path, item: Any) -> None:
    """Best-effort TD-031 ledger recording of an ``approved`` outcome.

    Only records for producer-origin ``self_apply_lane.run`` items, only the
    ``approved`` outcome (this hook runs on the approve path only), and swallows
    any registry failure so approval flow is never broken.
    """
    try:
        if getattr(item, "operation", None) != "self_apply_lane.run":
            return
        from core.self_build_producer import PRODUCER_ORIGIN
        payload = getattr(item, "payload", None)
        origin = payload.get("origin") if isinstance(payload, dict) else None
        if origin != PRODUCER_ORIGIN:
            return
        from core.subagent_registry import SubagentRegistry
        registry = SubagentRegistry.load(workspace)
        registry.apply_lane_outcome(getattr(item, "id", None), "approved")
    except Exception:
        pass


def _handle_approval_abort(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    item_id = rest.strip()
    if not item_id:
        print("Usage: :approval-abort <approval_id>", file=sys.stderr)
        return True
    inbox = _approval_inbox_for(agent, workspace)
    try:
        item = inbox.abort(item_id)
    except KeyError as exc:
        print(f"(approval abort failed: {exc})", file=sys.stderr)
        return True
    agent.log.log("approval_inbox_decision", item.to_dict())
    print(f"(approval aborted: {item.id}; operation={item.operation})", file=sys.stderr)
    return True


def _handle_approval_run(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    item_id = rest.strip()
    if not item_id:
        print("Usage: :approval-run <approval_id>", file=sys.stderr)
        return True
    inbox = _approval_inbox_for(agent, workspace)
    item = inbox.get(item_id)
    if item is None:
        print(f"(approval run failed: approval not found: {item_id})", file=sys.stderr)
        return True
    if item.status != "approved":
        print(
            f"(approval run refused: {item.id} status={item.status}; approve it first)",
            file=sys.stderr,
        )
        return True
    if item.operation != "autonomous_runtime.allow_effects":
        print(
            f"(approval run refused: unsupported operation={item.operation})",
            file=sys.stderr,
        )
        return True

    payload = item.payload
    try:
        config = AutonomousRuntimeConfig(
            goal=str(payload.get("goal") or "project health"),
            dry_run=False,
            effects_approved=True,
            limit=max(1, int(payload.get("limit", 5))),
            include_tests=_payload_bool(payload.get("include_tests"), default=True),
            learning_limit=max(1, int(payload.get("learning_limit", 5))),
        )
    except (TypeError, ValueError) as exc:
        print(f"(approval run failed: invalid payload: {exc})", file=sys.stderr)
        return True

    report = AutonomousRuntime(
        agent,
        workspace=workspace,
        approval_inbox=inbox,
    ).run(config)
    if report.status == "completed":
        executed = inbox.mark_executed(item.id)
        agent.log.log("approval_inbox_executed", executed.to_dict())
    print(report.user_summary(), file=sys.stderr)
    return True
