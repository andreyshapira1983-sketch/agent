from __future__ import annotations

from pathlib import Path
from typing import Any

from core.best_next_action import BestNextAction
from core.campaign_types import CampaignActionOutcome, CampaignConfig


def _log(agent: Any, event: str, payload: dict[str, Any]) -> None:
    log = getattr(agent, "log", None)
    if log is None:
        return
    try:
        log.log(event, payload)
    except (AttributeError, TypeError):
        pass


def _cost_totals(agent: Any) -> tuple[int, int]:
    try:
        usage_ledger = getattr(agent.model_router, "usage_ledger", None)
        ledger = getattr(usage_ledger, "budget_ledger", None)
        if ledger is None:
            return (0, 0)
        totals = ledger.snapshot().get("totals", {})
        return (int(totals.get("llm_calls", 0)), int(totals.get("model_cost_units", 0)))
    except (AttributeError, TypeError, ValueError):
        return (0, 0)


def _action_focused_goal(goal: str, action: BestNextAction) -> str:
    reason = (action.reason or "").strip()
    evidence = "; ".join(e for e in action.evidence[:3] if e)
    parts = [
        f"Campaign goal: {goal.strip()}.",
        f"The single highest-priority signal right now is '{action.action}' — {action.title}.",
    ]
    if reason:
        parts.append(f"Why it matters: {reason}")
    if evidence:
        parts.append(f"Evidence: {evidence}.")
    parts.append(
        "Decide the ONE most useful next step a human should take to move the goal forward, and justify it with the evidence. Reason read-only — do not perform any effects."
    )
    return " ".join(parts)


def _default_gather_signals(agent: Any, workspace: Any, approval_inbox: Any) -> dict[str, Any]:
    import agent_tick
    from core.alert_ack import AlertAckStore
    from core.approval_inbox import ApprovalInbox
    from core.approval_triage import triage_inbox
    from core.best_next_action import select_best_next_action

    ws = Path(workspace)
    heartbeat = agent_tick._read_heartbeat(ws)
    age = agent_tick._heartbeat_age_seconds(heartbeat)
    hb = heartbeat or {}
    inbox = approval_inbox or ApprovalInbox(path=ws / "data" / "approval_inbox.jsonl")
    triage = triage_inbox(inbox.pending())
    ack_store = AlertAckStore(path=ws / "data" / "alert_acknowledgements.jsonl")
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
    return {"heartbeat": hb, "age": age, "triage": triage, "action": action}


def _default_execute_action(
    *,
    agent: Any,
    workspace: Any,
    action: BestNextAction,
    config: CampaignConfig,
    approval_inbox: Any = None,
) -> CampaignActionOutcome:
    from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig
    from core.budget_governor import BudgetLimits

    llm_before, cost_before = _cost_totals(agent)
    focused_goal = _action_focused_goal(config.goal, action)
    runtime = AutonomousRuntime(agent, workspace=workspace, approval_inbox=approval_inbox)
    report = runtime.run(
        AutonomousRuntimeConfig(
            goal=focused_goal,
            dry_run=config.dry_run,
            limit=3,
            include_tests=False,
            include_goal=True,
            budgets=BudgetLimits(max_agent_runs=1),
            enable_reflection=False,
        )
    )
    llm_after, cost_after = _cost_totals(agent)
    pending = 0
    try:
        pending = int(report.approvals.get("pending", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        pending = 0
    proposal = f"approvals_pending={pending}" if pending else None
    artifact = None
    for task_report in getattr(report, "tasks", []) or []:
        if getattr(task_report.task, "kind", "") == "goal":
            answer = (task_report.details or {}).get("answer")
            if answer:
                digest = " ".join(str(answer).split())[:160]
                if digest:
                    artifact = f"reasoning: {digest}"
            break
    return CampaignActionOutcome(
        result=report.status,
        llm_calls_spent=max(0, llm_after - llm_before),
        cost_units_spent=max(0, cost_after - cost_before),
        proposal=proposal,
        artifact=artifact,
    )
