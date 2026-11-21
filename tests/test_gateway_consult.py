"""Tests for gateway hard-stop consult helpers (G5a)."""
from __future__ import annotations

from core.gateway_consult import (
    budget_enforcement_blockers,
    readiness_blockers,
)


def test_readiness_blockers_pending_and_budget() -> None:
    blockers = readiness_blockers(
        pending_approvals=1,
        budget_snapshot=None,
    )
    assert "1 approval item(s) pending" in blockers
    assert any("budget" in b.casefold() for b in blockers)


def test_budget_enforcement_blockers_over_limit() -> None:
    snapshot = {
        "windows": [
            {
                "name": "day",
                "counters": {
                    "llm_calls": {"used": 10, "limit": 10},
                },
            }
        ],
        "totals": {},
    }
    blockers = budget_enforcement_blockers(snapshot)
    assert blockers == (
        "persistent budget usage is already at or above a configured limit",
    )
