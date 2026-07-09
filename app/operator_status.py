"""Read-only operator status REPL handlers and digest formatters.

Split out of ``main.py``. Collects architecture/runtime/queue/scheduler/budget
state and prints operator-facing reports. Does not own task-queue dispatch,
scheduler mutations, or approval decision handlers.

``main.py`` re-exports the public handlers (and ``_operator_digest_payload`` for
other handlers that still live in ``main``).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.task_scheduler_cli import _scheduler_for, _task_queue_for
from cli.commands_approval import _approval_inbox_for
from cli.commands_budget import (
    _autonomy_readiness_payload,
    _budget_enforcement_status,
    _budget_ledger_snapshot,
    _format_autonomy_readiness,
    _format_operator_budget_digest,
    _next_action_prerequisites,
)
from cli.parsers import _split_meta_args
from core.architecture_audit import audit_architecture
from core.autonomous_runtime import AutonomousRuntime
from core.loop import AgentLoop


def _operator_digest_payload(agent: AgentLoop, workspace: Path) -> dict:
    audit = audit_architecture(workspace)
    runtime_status = AutonomousRuntime(
        agent,
        workspace=workspace,
        approval_inbox=_approval_inbox_for(agent, workspace),
    ).status()
    task_queue = _task_queue_for(agent, workspace).summary()
    scheduler = _scheduler_for(agent, workspace).summary()
    model_usage = agent.model_router.usage_snapshot()
    budget_windows = _budget_ledger_snapshot(agent)
    payload = {
        "architecture": audit.to_dict(),
        "runtime": runtime_status,
        "task_queue": task_queue,
        "scheduler": scheduler,
        "model_usage": model_usage,
        "persistent_budget_windows": budget_windows,
        "budget_policy": _budget_enforcement_status(budget_windows),
    }
    payload["recommendations"] = _operator_recommendations(payload)
    return payload


def _operator_recommendations(payload: dict) -> list[str]:
    recommendations: list[str] = []
    budget_policy = payload.get("budget_policy")
    if not isinstance(budget_policy, dict):
        budget_policy = _budget_enforcement_status(
            payload.get("persistent_budget_windows")
        )
    if budget_policy.get("warning"):
        recommendations.append(str(budget_policy["warning"]))
    approvals = payload.get("runtime", {}).get("approval_inbox", {})
    pending = int(approvals.get("pending", 0) or 0)
    if pending:
        recommendations.append(
            f"Review {pending} pending approval item(s) before allowing effects."
        )
    architecture = payload.get("architecture", {})
    gaps = architecture.get("priority_gaps", [])
    if gaps:
        first = gaps[0]
        recommendations.append(
            f"Next architecture gap: {first.get('title')} - {first.get('next_step')}"
        )
    source_registry = payload.get("runtime", {}).get("source_registry", {})
    memory_records = int(payload.get("runtime", {}).get("persistent_memory_records", 0) or 0)
    if int(source_registry.get("claims", 0) or 0) and memory_records == 0:
        recommendations.append(
            "Promote only reviewed source-backed claims into persistent memory; auto-write is still off."
        )
    task_queue = payload.get("task_queue", {})
    scheduler = payload.get("scheduler", {})
    if not task_queue.get("pending_due") and not scheduler.get("due"):
        recommendations.append(
            "No due scheduled work is waiting; run a dry health pass when you want active verification."
        )
    return recommendations


def _format_operator_digest(payload: dict) -> str:
    architecture = payload.get("architecture", {})
    runtime = payload.get("runtime", {})
    source_registry = runtime.get("source_registry", {})
    approvals = runtime.get("approval_inbox", {})
    model_usage = payload.get("model_usage") or {}
    totals = model_usage.get("totals", {})
    session_totals = model_usage.get("session_totals", {})
    task_queue = payload.get("task_queue", {})
    scheduler = payload.get("scheduler", {})
    lines = [
        "=== operator digest ===",
        (
            "architecture: "
            f"ready_for_multi_agent_execution={architecture.get('ready_for_multi_agent_execution')} "
            f"status_counts={architecture.get('status_counts', {})}"
        ),
        (
            "source registry: "
            f"sources={source_registry.get('sources', 0)} "
            f"claims={source_registry.get('claims', 0)}"
        ),
        f"persistent memory: records={runtime.get('persistent_memory_records', 0)}",
        (
            "approvals: "
            f"pending={approvals.get('pending', 0)} total={approvals.get('total', 0)}"
        ),
        (
            "queue/scheduler: "
            f"pending_due={task_queue.get('pending_due', 0)} "
            f"scheduled_due={scheduler.get('due', 0)}"
        ),
        (
            "model usage: "
            f"history_calls={totals.get('calls', 0)} "
            f"history_tokens={totals.get('total_tokens', 0)} "
            f"session_calls={session_totals.get('calls', 0)} "
            f"session_tokens={session_totals.get('total_tokens', 0)}"
        ),
    ]
    gaps = architecture.get("priority_gaps", [])
    if gaps:
        lines.append("attention:")
        for gap in gaps[:3]:
            lines.append(f"  - {gap.get('title')}: {gap.get('summary')}")
            lines.append(f"    next: {gap.get('next_step')}")
    else:
        lines.append("attention: none")
    recommendations = payload.get("recommendations", [])
    if recommendations:
        lines.append("recommended actions:")
        for item in recommendations[:5]:
            lines.append(f"  - {item}")
    return "\n".join(lines)


def _handle_operator_check(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :operator-check [--json]", file=sys.stderr)
        return True
    payload = _operator_digest_payload(agent, workspace)
    agent.log.log("operator_digest", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_operator_digest(payload), file=sys.stderr)
    return True


def _handle_operator_budget(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :operator-budget [--json]", file=sys.stderr)
        return True
    payload = _operator_digest_payload(agent, workspace)
    budget_payload = {
        "model_usage": payload.get("model_usage"),
        "persistent_budget_windows": payload.get("persistent_budget_windows"),
        "budget_policy": payload.get("budget_policy"),
        "recommendations": [
            item for item in payload.get("recommendations", [])
            if any(
                term in item.casefold()
                for term in ("budget limits", "hour/day", "model", "token", "cost", "spend")
            )
        ],
    }
    agent.log.log("operator_budget_digest", budget_payload)
    if as_json:
        print(json.dumps(budget_payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_operator_budget_digest(budget_payload), file=sys.stderr)
    return True


def _handle_urgent_status(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :urgent-status [--json]", file=sys.stderr)
        return True
    payload = _operator_digest_payload(agent, workspace)
    urgent_payload = {
        "approval_inbox": payload.get("runtime", {}).get("approval_inbox", {}),
        "task_queue": payload.get("task_queue", {}),
        "scheduler": payload.get("scheduler", {}),
        "runtime_recommendations": payload.get("recommendations", []),
    }
    agent.log.log("operator_urgent_status", urgent_payload)
    if as_json:
        print(json.dumps(urgent_payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_urgent_status(urgent_payload), file=sys.stderr)
    return True


def _format_urgent_status(payload: dict) -> str:
    approvals = payload.get("approval_inbox", {})
    queue = payload.get("task_queue", {})
    scheduler = payload.get("scheduler", {})
    urgent_items: list[str] = []
    pending = int(approvals.get("pending", 0) or 0)
    due_tasks = int(queue.get("pending_due", 0) or 0)
    due_schedules = int(scheduler.get("due", 0) or 0)
    if pending:
        urgent_items.append(f"{pending} approval item(s) are waiting.")
    if due_tasks:
        urgent_items.append(f"{due_tasks} queued task(s) are due.")
    if due_schedules:
        urgent_items.append(f"{due_schedules} schedule item(s) are due.")
    lines = [
        "=== urgent status ===",
        f"approvals_pending={pending}",
        f"queue_pending_due={due_tasks}",
        f"scheduler_due={due_schedules}",
    ]
    if urgent_items:
        lines.append("urgent items:")
        lines.extend(f"  - {item}" for item in urgent_items)
    else:
        lines.append("urgent items: none")
    return "\n".join(lines)


def _handle_next_actions(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :next-actions [--json]", file=sys.stderr)
        return True
    payload = _operator_digest_payload(agent, workspace)
    next_payload = {
        "prerequisites": _next_action_prerequisites(payload),
        "priority_gaps": payload.get("architecture", {}).get("priority_gaps", []),
        "recommendations": payload.get("recommendations", []),
    }
    agent.log.log("operator_next_actions", next_payload)
    if as_json:
        print(json.dumps(next_payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_next_actions(next_payload), file=sys.stderr)
    return True


def _format_next_actions(payload: dict) -> str:
    lines = ["=== next actions ==="]
    prerequisites = payload.get("prerequisites", [])
    if prerequisites:
        lines.append("prerequisites before long work sessions:")
        for item in prerequisites[:5]:
            lines.append(f"  - {item}")
    gaps = payload.get("priority_gaps", [])
    if gaps:
        lines.append("architecture priorities:")
        for gap in gaps[:3]:
            lines.append(f"  - {gap.get('title')}: {gap.get('next_step')}")
    else:
        lines.append("architecture priorities: none")
    recommendations = payload.get("recommendations", [])
    if recommendations:
        lines.append("recommended actions:")
        for item in recommendations[:5]:
            lines.append(f"  - {item}")
    return "\n".join(lines)


def _handle_autonomy_readiness(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :autonomy-readiness [--json]", file=sys.stderr)
        return True
    payload = _operator_digest_payload(agent, workspace)
    readiness_payload = _autonomy_readiness_payload(payload)
    agent.log.log("operator_autonomy_readiness", readiness_payload)
    if as_json:
        print(json.dumps(readiness_payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_autonomy_readiness(readiness_payload), file=sys.stderr)
    return True
