"""Gateway hard-stop consult helpers (G5a).

Pure, deterministic inputs for :mod:`core.actuation_gateway` — kill-switch and
readiness blockers. Keeps gateway wiring out of CLI modules.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


def _budget_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def budget_enforcement_blockers(snapshot: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Mirror :autonomy-readiness budget-related blockers (deterministic)."""
    if not isinstance(snapshot, Mapping):
        return (
            "persistent hour/day budget windows are not enabled",
        )
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
    if not enforcement_enabled:
        if usage_recorded:
            return (
                "persistent budget tracking is active but enforcement is disabled",
            )
        return ("persistent hour/day budget windows are not enabled",)
    breaches = [
        counter
        for counter in counters
        if _budget_int(counter.get("limit", 0)) > 0
        and _budget_int(counter.get("used", 0)) >= _budget_int(counter.get("limit", 0))
    ]
    if breaches:
        return ("persistent budget usage is already at or above a configured limit",)
    return ()


def readiness_blockers(
    *,
    pending_approvals: int = 0,
    budget_snapshot: Mapping[str, Any] | None = None,
    architecture_ready_for_multi_agent: bool = True,
) -> tuple[str, ...]:
    """Build readiness blocker strings (same rules as :autonomy-readiness)."""
    blockers: list[str] = []
    if pending_approvals > 0:
        blockers.append(f"{pending_approvals} approval item(s) pending")
    blockers.extend(budget_enforcement_blockers(budget_snapshot))
    if not architecture_ready_for_multi_agent:
        blockers.append("multi-agent/long-session readiness gaps remain")
    return tuple(blockers)


def budget_ledger_snapshot(workspace: Path) -> dict | None:
    """Best-effort persistent budget snapshot for gateway consult."""
    try:
        from core.budget_ledger import BudgetLedger

        root = Path(workspace).resolve()
        ledger = BudgetLedger.from_env(
            path=root / "data" / "budget_ledger.jsonl",
            config_path=root / "config" / "budget_limits.json",
        )
        return ledger.snapshot()
    except Exception:
        return None


def collect_hard_stop_reasons(
    *,
    kill_switch: Any | None,
    budget_snapshot: Mapping[str, Any] | None,
    readiness_blockers: tuple[str, ...] = (),
    check_readiness: bool = False,
) -> tuple[str, ...]:
    """Return non-empty reason tuple when gateway must ``block``."""
    reasons: list[str] = []
    if kill_switch is not None:
        state = kill_switch.status(budget_snapshot)
        if getattr(state, "active", False):
            detail = (
                getattr(state, "reason", None)
                or f"{getattr(state, 'window', '')}:{getattr(state, 'counter', '')} "
                f"{getattr(state, 'used', '')}/{getattr(state, 'limit', '')}"
            )
            reasons.append(f"kill_switch_active: {detail}".strip())
    if check_readiness:
        for item in readiness_blockers:
            reasons.append(f"readiness_blocker: {item}")
    return tuple(reasons)
