"""Цель человека, брошенная после пустых заходов, — остановка idle_stall, а не healthy_idle/completed."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.best_next_action import BestNextAction
from core.campaign import (
    OPERATOR_GOAL_EMPTY_PASSES,
    PURSUE_GOAL,
    CampaignActionOutcome,
    CampaignConfig,
    run_campaign,
)

GOAL = "Подключи к себе проверенные локальные модели как инструменты, каждую с тестом"


def _menu_has_an_errand(agent, workspace, approval_inbox, goal="", exhausted_actions=frozenset()):
    return {"action": BestNextAction(
        action="discriminate_causal_claim", title="t", severity="medium", priority=59,
        reason="an own causal claim is open", decided_by="priority_table", grounds="retained_record")}


def _nothing_admissible(agent, workspace, approval_inbox, goal="", exhausted_actions=frozenset()):
    return {"action": BestNextAction(
        action="observe", title="Observe", severity="none", priority=0,
        reason="Nothing admissible for the chosen goal.", decided_by="no_candidate", grounds="operator_goal")}


def _reconnaissance(*, agent, workspace, action, config, approval_inbox=None):
    return CampaignActionOutcome(result="completed", llm_calls_spent=5,
                                 artifact="reasoning: read 13 docs", work_done=True)


def _run(tmp_path: Path, gather=_menu_has_an_errand, **cfg):
    return run_campaign(
        CampaignConfig(goal=GOAL, max_cycles=24, max_idle_streak=3, dry_run=False,
                       max_unproductive_streak=3, **cfg),
        agent=SimpleNamespace(log=None, compensation_log=[]), workspace=str(tmp_path),
        gather_signals=gather, execute_action=_reconnaissance,
        now_fn=lambda: datetime(2026, 9, 26, 12, 13, tzinfo=timezone.utc), sleep_fn=lambda _s: None,
    )


def test_an_operator_goal_left_after_empty_passes_is_not_reported_healthy(tmp_path: Path) -> None:
    """Так --goal строит agent_tick.run_paced_campaign: goal_first, без next_goal и без критерия."""
    result = _run(tmp_path, goal_first=True)

    assert [r.action for r in result.records][:OPERATOR_GOAL_EMPTY_PASSES] == [PURSUE_GOAL] * OPERATOR_GOAL_EMPTY_PASSES
    assert not result.stop_reason.startswith("healthy_idle"), result.stop_reason
    assert result.status == "stopped", (result.status, result.stop_reason)


def test_the_pursue_when_idle_mode_is_judged_the_same(tmp_path: Path) -> None:
    """Второй режим цели человека: меню пусто, цель берётся в простое."""
    result = _run(tmp_path, gather=_nothing_admissible, pursue_goal_when_idle=True)

    assert not result.stop_reason.startswith("healthy_idle"), result.stop_reason
    assert result.status == "stopped", (result.status, result.stop_reason)
