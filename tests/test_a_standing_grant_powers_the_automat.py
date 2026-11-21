"""Стоячий грант: одно «да» на неделю питает автомат — в стенах, с журналом.

Background: docs/CODE_NOTES.md, "One yes a week".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.approval_inbox import ApprovalInbox
from core.autonomous_runtime import (
    AutonomousRuntime,
    AutonomousRuntimeConfig,
)
from tests.test_autonomous_runtime import _agent


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "README.md").write_text("Project overview.", encoding="utf-8")
    return tmp_path


def _grant(inbox: ApprovalInbox, *, runs_per_day: int = 3, days: int = 7):
    item = inbox.add(
        operation="autonomous_runtime.standing_grant",
        summary=f"standing effects grant: {runs_per_day} runs/day",
        risk="irreversible",
        payload={"max_runs_per_day": runs_per_day},
        expires_at=(datetime.now(timezone.utc) + timedelta(days=days)).isoformat(),
    )
    return inbox.approve(item.id)


def _run(workspace: Path, inbox: ApprovalInbox):
    agent = _agent(workspace, with_tests=False)
    return AutonomousRuntime(agent, workspace=workspace, approval_inbox=inbox).run(
        AutonomousRuntimeConfig(
            goal="учебная цель автомата", dry_run=False,
            limit=2, include_tests=False,
        )
    )


def test_the_daily_cap_holds(workspace: Path):
    """Стены гранта: исчерпанный дневной лимит возвращает прежний мир —
    заявку на разовое «да», а не тихий пропуск.
    """
    inbox = ApprovalInbox()
    _grant(inbox, runs_per_day=1)

    first = _run(workspace, inbox)
    second = _run(workspace, inbox)

    assert first.status != "blocked"
    assert second.status == "blocked"
    assert "approval required" in second.stop_reason


def test_an_expired_grant_powers_nothing(workspace: Path):
    inbox = ApprovalInbox()
    _grant(inbox, days=-1)

    report = _run(workspace, inbox)

    assert report.status == "blocked"
