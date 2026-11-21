"""The operator's goal outranks the agent's own errands; only a real breakage wins.

Live run 26.09 12:27 UTC: 20 cycles under the operator's goal «connect the tested
local models», and goal_drove=0 — every cycle took an own errand from the menu
(improve_failure_to_idea_pipeline, 11× discriminate_causal_claim, 4× failed
birth_experiment_specs), because an operator goal was pursued only when the menu
was EMPTY. Drive goals had «goal first» since 2026-09-19; the operator's did not.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.best_next_action import BestNextAction
from core.campaign import (
    CampaignActionOutcome,
    CampaignConfig,
    run_campaign,
)

GOAL = "Подключи к себе проверенные локальные модели как инструменты, каждую с тестом"


class _OwnErrand:
    def __init__(self, severity: str) -> None:
        self.severity = severity

    def __call__(self, agent, workspace, approval_inbox, goal="", exhausted_actions=frozenset()):
        return {"action": BestNextAction(
            action="discriminate_causal_claim", title="t", severity=self.severity, priority=59,
            reason="an own causal claim is open", decided_by="priority_table", grounds="retained_record")}


def _record():
    seen: list[str] = []

    def execute(*, agent, workspace, action, config, approval_inbox=None):
        seen.append(action.action)
        return CampaignActionOutcome(result="completed", llm_calls_spent=2, work_done=True)
    return seen, execute


def _run(tmp_path: Path, severity: str) -> list[str]:
    seen, execute = _record()
    run_campaign(
        CampaignConfig(goal=GOAL, max_cycles=8, max_idle_streak=3, dry_run=False, max_unproductive_streak=0,
                       pursue_goal_when_idle=True, goal_first=True),
        agent=SimpleNamespace(log=None, compensation_log=[]), workspace=str(tmp_path),
        gather_signals=_OwnErrand(severity), execute_action=execute,
        now_fn=lambda: datetime(2026, 9, 26, 12, 27, tzinfo=timezone.utc), sleep_fn=lambda _s: None,
    )
    return seen


def test_a_real_breakage_still_comes_first(tmp_path: Path) -> None:
    assert _run(tmp_path, "high")[0] == "discriminate_causal_claim"
