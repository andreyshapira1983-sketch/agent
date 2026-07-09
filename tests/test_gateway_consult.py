"""Tests for gateway hard-stop consult helpers (G5a)."""
from __future__ import annotations

import json
from pathlib import Path

from core.gateway_consult import (
    budget_enforcement_blockers,
    budget_ledger_snapshot,
    collect_hard_stop_reasons,
    readiness_blockers,
)


class _ActiveKillSwitch:
    def status(self, snapshot=None, *, now_iso=None):
        from core.budget_kill_switch import KillSwitchState

        return KillSwitchState(active=True, reason="budget_kill_switch")


def test_collect_hard_stop_kill_switch() -> None:
    reasons = collect_hard_stop_reasons(
        kill_switch=_ActiveKillSwitch(),
        budget_snapshot=None,
    )
    assert reasons
    assert reasons[0].startswith("kill_switch_active:")


def test_collect_hard_stop_readiness() -> None:
    reasons = collect_hard_stop_reasons(
        kill_switch=None,
        budget_snapshot=None,
        readiness_blockers=("2 approval item(s) pending",),
        check_readiness=True,
    )
    assert reasons == ("readiness_blocker: 2 approval item(s) pending",)


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


def test_budget_ledger_snapshot_reads_workspace_config(workspace: Path) -> None:
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_dir.joinpath("budget_limits.json").write_text(
        json.dumps(
            {
                "windows": {
                    "hour": {"llm_calls": 10},
                    "day": {"llm_calls": 100},
                }
            }
        ),
        encoding="utf-8",
    )

    snapshot = budget_ledger_snapshot(workspace)

    assert snapshot is not None
    assert snapshot.get("config_path", "").endswith("budget_limits.json")
    assert readiness_blockers(budget_snapshot=snapshot) == ()
