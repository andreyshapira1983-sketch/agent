"""Budget pressure as a number: near the end of the budget, actions with consequences wait for a human."""
# Threshold is the existing "window nearly exhausted" rule (core/self_build_supervisor.is_budget_near_exhaustion); it adds reversible
# actions to the already human-gated irreversible ones. Reads are untouched; pressure never relaxes a brake.
from __future__ import annotations

from typing import Any


def pressure_of(ledger: Any) -> Any:
    """Pressure measurement for PolicyGate.pressure (app/bootstrap.py)."""
    return lambda: budget_pressure(ledger)


def budget_pressure(ledger: Any) -> str:
    """Reason for budget pressure ("" = no pressure or budget unreadable)."""
    from core.self_build_supervisor import hour_budget_headroom, is_budget_near_exhaustion

    try:
        snapshot = ledger.snapshot()
    except Exception:  # noqa: BLE001 — an unreadable budget journal does not invent pressure
        return ""
    near, reasons = is_budget_near_exhaustion(hour_budget_headroom(snapshot or {}))
    return "; ".join(reasons) if near else ""
