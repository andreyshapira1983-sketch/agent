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


# -- conversational capability / readiness reports ----------------------------
# Extracted verbatim from main.py. These answer plain-language operator
# questions ("what can you do", "are you ready to code", "what are your gaps",
# "find your weaknesses", "what is the next safe test") and are reached through
# the operator-intent bridge rather than as :commands -- except
# :coding-readiness, which has both entry points.


def _handle_operator_capability_check(agent: AgentLoop, workspace: Path) -> bool:
    payload = _operator_capability_payload(agent, workspace)
    agent.log.log("operator_capability_check", payload)
    print(_format_operator_capability_check(payload), file=sys.stderr)
    return True


def _runtime_capability_facts(agent: AgentLoop) -> dict:
    """Live runtime introspection: what THIS running agent actually has wired.

    Reads the agent's own tool registry and memory handles (the same source
    session_start logs), so a "what can you do now" question is answered from
    the running process, not from README/web guesses (#2 introspection gap).
    """
    try:
        tools = sorted(tool.name for tool in agent.registry.list())
    except Exception:  # noqa: BLE001 — introspection must never crash the command
        tools = []
    return {
        "registered_tools": tools,
        "tool_count": len(tools),
        "memory": "on" if getattr(agent, "memory", None) is not None else "off",
        "persistent_memory": "on" if getattr(agent, "persistent_store", None) is not None else "off",
    }


def _operator_capability_payload(agent: AgentLoop, workspace: Path) -> dict:
    digest = _operator_digest_payload(agent, workspace)
    architecture = digest.get("architecture", {})
    runtime = digest.get("runtime", {})
    source_registry = runtime.get("source_registry", {})
    approvals = runtime.get("approval_inbox", {})
    queue = digest.get("task_queue", {})
    scheduler = digest.get("scheduler", {})
    return {
        "runtime": _runtime_capability_facts(agent),
        "wired": [
            "local operator digest/status commands",
            f"source registry visible: sources={source_registry.get('sources', 0)} claims={source_registry.get('claims', 0)}",
            f"approval inbox visible: pending={approvals.get('pending', 0)} total={approvals.get('total', 0)}",
            f"task queue/scheduler visible: pending_due={queue.get('pending_due', 0)} scheduler_due={scheduler.get('due', 0)}",
            "model usage and persistent budget windows are inspectable",
        ],
        "dry_run_only": [
            "autonomous runtime health passes should stay dry-run until readiness is green",
            "learning/ingestion can preview sources without memory auto-write",
            "self-repair proposals can be reviewed before any apply step",
        ],
        "requires_approval": [
            "allow-effects autonomous runtime",
            "file writes, repair apply, shell execution, external sends, spending",
            "persistent memory promotion beyond explicit user-approved notes",
        ],
        "not_implemented_or_limited": [
            gap.get("title")
            for gap in architecture.get("priority_gaps", [])
            if gap.get("title")
        ],
        "recommendations": digest.get("recommendations", []),
    }


def _format_operator_capability_check(payload: dict) -> str:
    lines = ["=== operator capabilities ==="]
    runtime = payload.get("runtime") or {}
    if runtime:
        lines.append("live runtime:")
        lines.append(
            f"  - registered tools ({runtime.get('tool_count', 0)}): "
            + (", ".join(runtime.get("registered_tools", [])) or "none")
        )
        lines.append(
            f"  - memory={runtime.get('memory', 'off')} "
            f"persistent_memory={runtime.get('persistent_memory', 'off')}"
        )
    sections = (
        ("wired", "wired now"),
        ("dry_run_only", "dry-run / supervised only"),
        ("requires_approval", "requires approval"),
        ("not_implemented_or_limited", "not implemented / limited"),
        ("recommendations", "operator recommendations"),
    )
    for key, title in sections:
        lines.append(f"{title}:")
        items = payload.get(key, [])
        lines.extend(f"  - {item}" for item in (items or ["none"]))
    return "\n".join(lines)


def _handle_programming_readiness(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :coding-readiness [--json]", file=sys.stderr)
        return True
    payload = _programming_readiness_payload(agent, workspace)
    agent.log.log("operator_programming_readiness", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_programming_readiness(payload), file=sys.stderr)
    return True


def _programming_readiness_payload(agent: AgentLoop, workspace: Path) -> dict:
    digest = _operator_digest_payload(agent, workspace)
    tool_names = {tool.name for tool in agent.registry.list()}
    test_files = sorted((workspace / "tests").glob("test_*.py")) if (workspace / "tests").exists() else []
    core_files = {
        "operator_intent": (workspace / "core" / "operator_intent.py").exists(),
        "loop": (workspace / "core" / "loop.py").exists(),
        "repair_proposal": (workspace / "core" / "repair_proposal.py").exists(),
        "governance": (workspace / "core" / "governance.py").exists(),
    }
    can_inspect = "file_read" in tool_names
    can_multi_file_review = bool(core_files["loop"])
    can_propose_patch = bool(core_files["repair_proposal"]) or "diff_file" in tool_names
    can_run_tests = "run_tests" in tool_names or bool(test_files)
    can_explain_rollback = bool(core_files["repair_proposal"]) or "file_write" in tool_names
    status = (
        "ready_for_read_only_programming_planning"
        if can_inspect and can_propose_patch and can_run_tests
        else "limited"
    )
    return {
        "status": status,
        "source_registry": digest.get("runtime", {}).get("source_registry", {}),
        "architecture": {
            "status_counts": digest.get("architecture", {}).get("status_counts", {}),
            "priority_gaps": digest.get("architecture", {}).get("priority_gaps", []),
        },
        "tooling": {
            "registered_tools": sorted(tool_names),
            "file_read": "file_read" in tool_names,
            "diff_file": "diff_file" in tool_names,
            "run_tests": "run_tests" in tool_names,
            "file_write": "file_write" in tool_names,
            "shell_exec": "shell_exec" in tool_names,
        },
        "test_availability": {
            "tests_dir_exists": (workspace / "tests").exists(),
            "pytest_ini_exists": (workspace / "pytest.ini").exists(),
            "test_file_count": len(test_files),
        },
        "capabilities": {
            "can_inspect_files_read_only": can_inspect,
            "can_use_explicit_multi_file_review": can_inspect and can_multi_file_review,
            "can_propose_patch_read_only": can_propose_patch,
            "can_name_targeted_tests": can_run_tests,
            "can_explain_rollback_boundary": can_explain_rollback,
            "can_estimate_risk": True,
        },
        "safe_small_task": (
            "Read explicitly mentioned workspace files, produce a read-only patch "
            "proposal, name targeted tests, and stop before apply/write."
        ),
        "files_to_read_first": [
            "the user-mentioned target file(s)",
            "the nearest existing tests for that behavior",
            "core/operator_intent.py when routing/operator wording is involved",
            "main.py when CLI/meta-command dispatch is involved",
            "core/loop.py when planner/tool/evidence flow is involved",
        ],
        "tests_to_run": [
            "targeted pytest for the touched behavior, e.g. pytest tests/test_operator_intent.py tests/test_cli.py -q",
            "full pytest before commit",
        ],
        "risk_estimation": [
            "read-only inspection and patch proposal are low risk",
            "test execution is medium operational risk because it uses local compute/time",
            "file writes, shell execution, repair apply and rollback touch state and require approval",
        ],
        "requires_approval": [
            "file_write or editing project files",
            "shell_exec beyond explicit user-run hints",
            "repair apply / self-repair controller write phase",
            "rollback that changes files",
            "allow-effects autonomous runtime",
            "persistent memory promotion",
        ],
        "do_not_do_yet": [
            "do not turn this into a generic project health report",
            "do not start Long Work Session Mode from a coding-readiness question",
            "do not use README/docs unless explicitly requested",
            "do not call LLM if local readiness is enough",
        ],
    }


def _format_programming_readiness(payload: dict) -> str:
    lines = [
        "=== programming readiness ===",
        f"status: {payload.get('status')}",
    ]
    registry = payload.get("source_registry", {})
    lines.append(
        "source registry: "
        f"sources={registry.get('sources', 0)} claims={registry.get('claims', 0)}"
    )
    tests = payload.get("test_availability", {})
    lines.append(
        "tests: "
        f"dir={tests.get('tests_dir_exists')} "
        f"pytest_ini={tests.get('pytest_ini_exists')} "
        f"files={tests.get('test_file_count')}"
    )
    lines.append("capabilities:")
    for key, value in (payload.get("capabilities") or {}).items():
        lines.append(f"  - {key}: {value}")
    lines.append(f"safe small task: {payload.get('safe_small_task')}")
    lines.append("files to read first:")
    lines.extend(f"  - {item}" for item in payload.get("files_to_read_first", []))
    lines.append("tests to run:")
    lines.extend(f"  - {item}" for item in payload.get("tests_to_run", []))
    lines.append("risk estimation:")
    lines.extend(f"  - {item}" for item in payload.get("risk_estimation", []))
    lines.append("requires approval:")
    lines.extend(f"  - {item}" for item in payload.get("requires_approval", []))
    lines.append("do not do yet:")
    lines.extend(f"  - {item}" for item in payload.get("do_not_do_yet", []))
    return "\n".join(lines)


def _handle_operator_gaps_check(agent: AgentLoop, workspace: Path) -> bool:
    digest = _operator_digest_payload(agent, workspace)
    readiness = _autonomy_readiness_payload(digest)
    payload = {
        "architecture": digest.get("architecture", {}),
        "readiness": readiness,
        "budget_policy": digest.get("budget_policy", {}),
        "next_actions": {
            "prerequisites": _next_action_prerequisites(digest),
            "recommendations": digest.get("recommendations", []),
        },
    }
    agent.log.log("operator_current_gaps_check", payload)
    print(_format_operator_gaps_check(payload), file=sys.stderr)
    return True


def _format_operator_gaps_check(payload: dict) -> str:
    architecture = payload.get("architecture", {})
    readiness = payload.get("readiness", {})
    budget = payload.get("budget_policy", {})
    next_actions = payload.get("next_actions", {})
    lines = [
        "=== current gaps ===",
        (
            "architecture: "
            f"ready_for_multi_agent_execution={architecture.get('ready_for_multi_agent_execution')} "
            f"status_counts={architecture.get('status_counts', {})}"
        ),
        (
            "autonomy readiness: "
            f"state={readiness.get('state')} "
            f"dry_run_runtime_ready={readiness.get('dry_run_runtime_ready')}"
        ),
        (
            "budget: "
            f"tracking={budget.get('tracking_enabled')} "
            f"limits_configured={budget.get('enforcement_enabled')} "
            f"over_limit={budget.get('over_limit')}"
        ),
    ]
    gaps = architecture.get("priority_gaps", [])
    lines.append("priority gaps:")
    if gaps:
        for gap in gaps[:5]:
            lines.append(f"  - {gap.get('title')}: {gap.get('next_step')}")
    else:
        lines.append("  - none")
    blockers = readiness.get("blockers", [])
    lines.append("readiness blockers:")
    lines.extend(f"  - {item}" for item in (blockers or ["none"]))
    prereqs = next_actions.get("prerequisites", [])
    lines.append("prerequisites:")
    lines.extend(f"  - {item}" for item in (prereqs or ["none"]))
    return "\n".join(lines)


def _handle_operator_weakness_finder(agent: AgentLoop, workspace: Path) -> bool:
    digest = _operator_digest_payload(agent, workspace)
    readiness = _autonomy_readiness_payload(digest)
    weaknesses: list[str] = []
    budget = digest.get("budget_policy", {})
    if budget.get("warning"):
        weaknesses.append(str(budget["warning"]))
    weaknesses.extend(readiness.get("blockers", []))
    for gap in digest.get("architecture", {}).get("priority_gaps", [])[:3]:
        title = gap.get("title")
        next_step = gap.get("next_step")
        if title:
            weaknesses.append(f"{title}: {next_step}")
    if not weaknesses:
        weaknesses.append("No live blocker found for local dry-run operator checks.")
    payload = {
        "weaknesses": weaknesses,
        "safe_boundary": [
            "keep allow-effects disabled",
            "prefer local operator status before README/docs synthesis",
            "use dry-run runtime until readiness and budget limits are configured",
        ],
    }
    agent.log.log("operator_weakness_finder", payload)
    print(_format_operator_weakness_finder(payload), file=sys.stderr)
    return True


def _format_operator_weakness_finder(payload: dict) -> str:
    lines = ["=== live weakness digest ===", "weaknesses:"]
    lines.extend(f"  - {item}" for item in payload.get("weaknesses", []))
    lines.append("safe boundary:")
    lines.extend(f"  - {item}" for item in payload.get("safe_boundary", []))
    return "\n".join(lines)


def _handle_next_safe_test(agent: AgentLoop, workspace: Path) -> bool:
    digest = _operator_digest_payload(agent, workspace)
    payload = {
        "recommended_test": (
            "Run a local no-web operator sanity check: "
            ":operator-check, :operator-budget, :autonomy-readiness, "
            ":next-actions, :source-registry."
        ),
        "why": [
            "It exercises live local state without README synthesis.",
            "It does not require web, shell execution, file writes, repair, or allow-effects.",
            "It confirms budget/readiness/source-registry signals before long work sessions.",
        ],
        "prerequisites": _next_action_prerequisites(digest),
        "avoid": [
            "do not start Long Work Session Mode from this question",
            "do not read README unless the user explicitly asks for documentation",
            "do not run repair/apply or allow-effects",
        ],
    }
    agent.log.log("operator_next_safe_test", payload)
    print(_format_next_safe_test(payload), file=sys.stderr)
    return True


def _format_next_safe_test(payload: dict) -> str:
    lines = [
        "=== next safe test ===",
        f"recommended: {payload.get('recommended_test')}",
        "why:",
    ]
    lines.extend(f"  - {item}" for item in payload.get("why", []))
    prereqs = payload.get("prerequisites", [])
    lines.append("prerequisites to watch:")
    lines.extend(f"  - {item}" for item in (prereqs or ["none"]))
    lines.append("avoid:")
    lines.extend(f"  - {item}" for item in payload.get("avoid", []))
    return "\n".join(lines)
