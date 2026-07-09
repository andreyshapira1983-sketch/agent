"""Budget / autonomy-readiness REPL commands and their pure helpers.

Split out of ``main.py``. These functions form a self-contained group: they
only call each other, ``cli.parsers._split_meta_args``, and ``core`` classes —
never back into ``main``. The two hybrid handlers that also need the operator
digest (``_handle_operator_budget`` / ``_handle_autonomy_readiness``) stay in
``main.py`` to avoid an import cycle.

``main.py`` re-exports every name below, so existing imports and the REPL
dispatch keep working unchanged.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from cli.parsers import _split_meta_args
from core.budget_governor import BudgetGovernor, BudgetLimits

if TYPE_CHECKING:
    from core.loop import AgentLoop


_BUDGET_ENV_VARS = (
    "AGENT_BUDGET_HOUR_LLM_CALLS",
    "AGENT_BUDGET_HOUR_MODEL_TOKENS",
    "AGENT_BUDGET_HOUR_MODEL_COST_UNITS",
    "AGENT_BUDGET_HOUR_WEB_FETCHES",
    "AGENT_BUDGET_DAY_LLM_CALLS",
    "AGENT_BUDGET_DAY_MODEL_TOKENS",
    "AGENT_BUDGET_DAY_MODEL_COST_UNITS",
    "AGENT_BUDGET_DAY_WEB_FETCHES",
)


def _budget_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _budget_limit_text(value: object) -> str:
    limit = _budget_int(value)
    return str(limit) if limit > 0 else "unlimited"


def _budget_enforcement_status(snapshot: dict | None) -> dict:
    if not isinstance(snapshot, dict):
        return {
            "tracking_enabled": False,
            "enforcement_enabled": False,
            "usage_recorded": False,
            "all_limits_zero": True,
            "over_limit": False,
            "limit_breaches": [],
            "warning": (
                "Persistent budget ledger is not enabled; long unattended "
                "sessions have no cross-run spend stop."
            ),
        }
    windows_raw = snapshot.get("windows", [])
    windows = windows_raw if isinstance(windows_raw, list) else []
    counters = [
        counter
        for window in windows
        if isinstance(window, dict)
        for counter in (window.get("counters", {}) or {}).values()
        if isinstance(counter, dict)
    ]
    totals = snapshot.get("totals", {})
    if not isinstance(totals, dict):
        totals = {}
    usage_recorded = any(
        _budget_int(counter.get("used", 0)) > 0 for counter in counters
    ) or any(_budget_int(value) > 0 for value in totals.values())
    enforcement_enabled = any(
        _budget_int(counter.get("limit", 0)) > 0 for counter in counters
    )
    breaches = [
        {
            "used": _budget_int(counter.get("used", 0)),
            "limit": _budget_int(counter.get("limit", 0)),
        }
        for counter in counters
        if _budget_int(counter.get("limit", 0)) > 0
        and _budget_int(counter.get("used", 0)) >= _budget_int(counter.get("limit", 0))
    ]
    tracking_enabled = bool(windows)
    all_limits_zero = tracking_enabled and not enforcement_enabled
    warning = ""
    if all_limits_zero:
        warning = (
            "Persistent budget tracking is active, but all hour/day limits are 0, "
            "so cross-run enforcement is disabled; set AGENT_BUDGET_HOUR_LLM_CALLS, "
            "AGENT_BUDGET_DAY_MODEL_TOKENS, or AGENT_BUDGET_DAY_MODEL_COST_UNITS "
            "before long unattended sessions."
        )
    elif not tracking_enabled:
        warning = (
            "Persistent budget ledger is not enabled; long unattended sessions "
            "have no cross-run spend stop."
        )
    elif breaches:
        warning = (
            "One or more persistent budget counters are already at or above "
            "their configured limit; the next matching expensive action will "
            "be blocked until the hour/day window rolls forward or limits are raised."
        )
    return {
        "tracking_enabled": tracking_enabled,
        "enforcement_enabled": enforcement_enabled,
        "usage_recorded": usage_recorded,
        "all_limits_zero": all_limits_zero,
        "over_limit": bool(breaches),
        "limit_breaches": breaches,
        "warning": warning,
    }


def _persistent_budget_limits_configured(snapshot: dict | None) -> bool:
    return bool(_budget_enforcement_status(snapshot).get("enforcement_enabled"))


def _budget_ledger_for(agent: AgentLoop):
    usage_ledger = getattr(agent.model_router, "usage_ledger", None)
    return getattr(usage_ledger, "budget_ledger", None)


def _budget_ledger_snapshot(agent: AgentLoop) -> dict | None:
    ledger = _budget_ledger_for(agent)
    if ledger is None:
        return None
    return ledger.snapshot()


def _budget_config_payload(agent: AgentLoop, workspace: Path) -> dict:
    snapshot = _budget_ledger_snapshot(agent)
    config_path = None
    if isinstance(snapshot, dict):
        config_path = snapshot.get("config_path")
    if not config_path:
        config_path = str((workspace / "config" / "budget_limits.json").resolve())
    config_file = Path(str(config_path))
    budget_policy = _budget_enforcement_status(snapshot)
    windows = snapshot.get("windows", []) if isinstance(snapshot, dict) else []
    return {
        "config_path": str(config_file),
        "config_exists": config_file.exists(),
        "example_path": str((workspace / "config" / "budget_limits.example.json").resolve()),
        "env_overrides": {
            name: {
                "set": bool(os.getenv(name)),
                "value": os.getenv(name) or "",
            }
            for name in _BUDGET_ENV_VARS
        },
        "effective_windows": windows,
        "budget_policy": budget_policy,
        "powershell_example": [
            '$env:AGENT_BUDGET_HOUR_LLM_CALLS="20"',
            '$env:AGENT_BUDGET_DAY_LLM_CALLS="100"',
            '$env:AGENT_BUDGET_DAY_MODEL_TOKENS="300000"',
            '$env:AGENT_BUDGET_DAY_MODEL_COST_UNITS="500"',
        ],
    }


def _format_budget_config(payload: dict) -> str:
    policy = payload.get("budget_policy") or {}
    lines = [
        "=== budget config ===",
        f"config_path: {payload.get('config_path')}",
        f"config_exists: {payload.get('config_exists')}",
        f"example_path: {payload.get('example_path')}",
        (
            "effective enforcement: "
            f"tracking={policy.get('tracking_enabled')} "
            f"limits_configured={policy.get('enforcement_enabled')} "
            f"usage_recorded={policy.get('usage_recorded')}"
        ),
    ]
    warning = policy.get("warning")
    if warning:
        lines.append("budget warning:")
        lines.append(f"  - {warning}")
    lines.append("effective windows:")
    windows = payload.get("effective_windows") or []
    if windows:
        for window in windows:
            counters = window.get("counters", {})
            configured = [
                f"{name}={data.get('used', 0)}/{data.get('limit', 0)}"
                for name, data in counters.items()
                if isinstance(data, dict)
                and (int(data.get("used", 0) or 0) or int(data.get("limit", 0) or 0))
            ]
            lines.append(
                f"  - {window.get('name')}: "
                + (", ".join(configured) if configured else "all limits 0")
            )
    else:
        lines.append("  - none")
    env_overrides = payload.get("env_overrides") or {}
    set_env = [name for name, info in env_overrides.items() if info.get("set")]
    lines.append("env overrides:")
    lines.extend(f"  - {name}=set" for name in set_env[:8])
    if not set_env:
        lines.append("  - none")
    if not policy.get("enforcement_enabled"):
        lines.append("PowerShell quick start:")
        lines.extend(f"  {item}" for item in payload.get("powershell_example", []))
    return "\n".join(lines)


def _format_operator_budget_digest(payload: dict) -> str:
    model_usage = payload.get("model_usage") or {}
    limits = model_usage.get("limits", {})
    totals = model_usage.get("totals", {})
    session = model_usage.get("session_totals", {})
    windows_payload = payload.get("persistent_budget_windows") or {}
    windows = windows_payload.get("windows", []) if isinstance(windows_payload, dict) else []
    budget_policy = payload.get("budget_policy") or _budget_enforcement_status(
        windows_payload if isinstance(windows_payload, dict) else None
    )
    lines = [
        "=== operator budget ===",
        (
            "model usage: "
            f"history_calls={totals.get('calls', 0)} "
            f"history_tokens={totals.get('total_tokens', 0)} "
            f"history_cost_units={totals.get('cost_units', 0)}"
        ),
        (
            "this session: "
            f"calls={session.get('calls', 0)} "
            f"tokens={session.get('total_tokens', 0)} "
            f"cost_units={session.get('cost_units', 0)}"
        ),
        (
            "session caps: "
            f"max_calls={limits.get('max_calls_label') or _budget_limit_text(limits.get('max_calls', 0))} "
            f"max_tokens={limits.get('max_tokens_label') or _budget_limit_text(limits.get('max_tokens', 0))} "
            f"max_cost_units={limits.get('max_cost_units_label') or _budget_limit_text(limits.get('max_cost_units', 0))}"
        ),
        (
            "persistent enforcement: "
            f"tracking={budget_policy.get('tracking_enabled')} "
            f"limits_configured={budget_policy.get('enforcement_enabled')} "
            f"usage_recorded={budget_policy.get('usage_recorded')}"
        ),
    ]
    if budget_policy.get("warning"):
        lines.append("budget warning:")
        lines.append(f"  - {budget_policy['warning']}")
    if windows:
        lines.append("persistent windows:")
        for window in windows:
            counters = window.get("counters", {})
            compact = ", ".join(
                f"{name}={data.get('used', 0)}/{data.get('limit_label') or _budget_limit_text(data.get('limit', 0))}"
                for name, data in counters.items()
                if isinstance(data, dict)
            )
            lines.append(f"  - {window.get('name')}: {compact}")
    else:
        lines.append("persistent windows: not enabled")
    recommendations = payload.get("recommendations", [])
    if recommendations:
        lines.append("budget recommendations:")
        for item in recommendations:
            lines.append(f"  - {item}")
    return "\n".join(lines)


def _autonomy_readiness_payload(payload: dict) -> dict:
    approvals = payload.get("runtime", {}).get("approval_inbox", {})
    architecture = payload.get("architecture", {})
    pending = int(approvals.get("pending", 0) or 0)
    budget_policy = payload.get("budget_policy")
    if not isinstance(budget_policy, dict):
        budget_policy = _budget_enforcement_status(
            payload.get("persistent_budget_windows")
        )
    budget_limits_configured = bool(budget_policy.get("enforcement_enabled"))
    blockers: list[str] = []
    if pending:
        blockers.append(f"{pending} approval item(s) pending")
    if not budget_limits_configured:
        if budget_policy.get("tracking_enabled"):
            blockers.append(
                "persistent budget tracking is active but enforcement is disabled"
            )
        else:
            blockers.append("persistent hour/day budget windows are not enabled")
    elif budget_policy.get("over_limit"):
        blockers.append("persistent budget usage is already at or above a configured limit")
    if not architecture.get("ready_for_multi_agent_execution"):
        blockers.append("multi-agent/long-session readiness gaps remain")
    state = "ready" if not blockers else "limited"
    return {
        "state": state,
        "dry_run_runtime_ready": True,
        "ready_for_multi_agent_execution": bool(
            architecture.get("ready_for_multi_agent_execution")
        ),
        "approvals_pending": pending,
        "persistent_budget_limits_configured": budget_limits_configured,
        "budget_policy": budget_policy,
        "blockers": blockers,
        "recommendations": payload.get("recommendations", []),
    }


def _next_action_prerequisites(payload: dict) -> list[str]:
    prerequisites: list[str] = []
    budget_policy = payload.get("budget_policy")
    if not isinstance(budget_policy, dict):
        budget_policy = _budget_enforcement_status(
            payload.get("persistent_budget_windows")
        )
    if budget_policy.get("warning"):
        prerequisites.append(str(budget_policy["warning"]))

    gaps = payload.get("architecture", {}).get("priority_gaps", [])
    has_long_work_gap = any(
        "long work session" in str(gap.get("title", "")).casefold()
        for gap in gaps
    )
    if has_long_work_gap:
        prerequisites.append(
            "Run a live state-store recovery drill before enabling Long Work Session Mode."
        )
    return prerequisites


def _format_autonomy_readiness(payload: dict) -> str:
    lines = [
        "=== autonomy readiness ===",
        f"state={payload.get('state')}",
        f"dry_run_runtime_ready={payload.get('dry_run_runtime_ready')}",
        f"ready_for_multi_agent_execution={payload.get('ready_for_multi_agent_execution')}",
        f"approvals_pending={payload.get('approvals_pending')}",
        (
            "persistent_budget_limits_configured="
            f"{payload.get('persistent_budget_limits_configured')}"
        ),
    ]
    budget_policy = payload.get("budget_policy") or {}
    if budget_policy.get("warning"):
        lines.append("budget warning:")
        lines.append(f"  - {budget_policy['warning']}")
    blockers = payload.get("blockers", [])
    if blockers:
        lines.append("blockers:")
        lines.extend(f"  - {item}" for item in blockers)
    else:
        lines.append("blockers: none")
    return "\n".join(lines)


def _handle_budget_config(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :budget-config [--json]", file=sys.stderr)
        return True
    payload = _budget_config_payload(agent, workspace)
    agent.log.log("operator_budget_config", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_budget_config(payload), file=sys.stderr)
    return True


def _handle_budget_status(agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    snapshot = BudgetGovernor(BudgetLimits()).snapshot()
    snapshot["model_usage"] = agent.model_router.usage_snapshot()
    snapshot["persistent_budget_windows"] = _budget_ledger_snapshot(agent)
    print("=== autonomous budget defaults ===", file=sys.stderr)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2), file=sys.stderr)
    return True


def _handle_budget_window_status(rest: str, agent: AgentLoop) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :budget-window-status [--json]", file=sys.stderr)
        return True
    ledger = _budget_ledger_for(agent)
    if ledger is None:
        print("(persistent budget ledger is not enabled)", file=sys.stderr)
        return True
    if as_json:
        print(json.dumps(ledger.snapshot(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(ledger.user_summary(), file=sys.stderr)
    return True


def _handle_budget_kill_switch(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Operator view / reset for the persistent budget kill-switch (TD-022).

    Read-only by default: reports whether the autonomous day budget kill-switch
    is engaged and why. ``--clear`` resets a latched switch (operator action);
    it does not change any budget limit, routing, or provider configuration.
    """
    from core.budget_kill_switch import BudgetKillSwitch, default_path

    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    do_clear = "--clear" in tokens
    if any(token not in {"--json", "--clear"} for token in tokens):
        print("Usage: :budget-kill-switch [--json] [--clear]", file=sys.stderr)
        return True

    kill_switch = BudgetKillSwitch(path=default_path(workspace))
    if do_clear:
        kill_switch.clear()
        agent.log.log("budget_kill_switch_cleared", {"path": str(default_path(workspace))})

    snapshot = _budget_ledger_snapshot(agent)
    state = kill_switch.status(snapshot)
    payload = state.to_dict()
    agent.log.log("budget_kill_switch_status", payload)

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return True

    lines = ["=== budget kill-switch ==="]
    lines.append(f"active: {payload['active']}")
    if payload["active"]:
        lines.append(f"reason: {payload['reason']}")
        lines.append(
            f"triggering: {payload['window']} {payload['counter']} "
            f"{payload['used']}/{payload['limit']} ({payload['limit_source']})"
        )
        lines.append(f"since: {payload['timestamp']}")
        lines.append("clear with: :budget-kill-switch --clear")
    else:
        lines.append("autonomous day budget within limits")
    print("\n".join(lines), file=sys.stderr)
    return True
