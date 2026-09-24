"""Выполненная цель закрывается сразу после цикла, который её выполнил.

Ночь 24→25.09 (logs/campaign_24h_stderr.log, циклы 15–27): объяснение
наблюдения записывалось в первом цикле, а кампания судила цель только при
смене — крутила «объяснять нечего», ловила повтор, спала 7.5 минут. За 25
минут — 2 полезных цикла из 13.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.campaign import run_campaign
from core.campaign_ledger import CampaignLedger
from core.campaign_types import CampaignActionOutcome, CampaignConfig


def _action(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        action=name, title=name, severity="medium", priority=59,
        risk="reversible", grounds="operator_goal", decided_by="test",
        next_check_at=None, reason="", evidence=(), target_path=None,
    )


def test_the_cycle_that_meets_the_goal_hands_over_to_the_next(tmp_path: Path) -> None:
    goals_seen: list[str] = []

    def execute(*, agent, workspace, action, config, approval_inbox=None):
        goals_seen.append(config.goal)
        note = Path(workspace) / "data" / "explained.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("объяснение", encoding="utf-8")
        return CampaignActionOutcome(result="completed", llm_calls_spent=1,
                                     cost_units_spent=1, subject="s", work_done=True,
                                     artifact="data/explained.md")

    goals = iter(["вторая цель"])
    run_campaign(
        CampaignConfig(goal="первая цель", success_check="появился файл data/explained.md",
                       max_cycles=3, max_idle_streak=3, dry_run=False),
        agent=SimpleNamespace(log=None), workspace=str(tmp_path),
        gather_signals=lambda *a, **k: {"action": _action("explain")},
        execute_action=execute, ledger=CampaignLedger(),
        next_goal=lambda: next(goals, ""),
        now_fn=lambda: datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
    )

    assert goals_seen[:2] == ["первая цель", "вторая цель"], (
        f"цель выполнена первым циклом, а второй цикл снова работал по ней: {goals_seen}")
