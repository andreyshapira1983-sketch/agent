"""An operator's goal keeps getting passes until three passes in a row wrote nothing.

Live run 26.09: the goal «connect five local models, each with a witness test» got
ONE agent pass, then three idle cycles and healthy_idle after 4 minutes. The first
fix reopened only after a writing pass; the restart showed the first pass is
reconnaissance (13 documents read, nothing written) and the goal closed again.
"""
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


def _nothing_admissible(agent, workspace, approval_inbox, goal="", exhausted_actions=frozenset()):
    return {"action": BestNextAction(
        action="observe", title="Observe", severity="none", priority=0,
        reason="Nothing admissible for the chosen goal.", decided_by="no_candidate", grounds="operator_goal")}


class _Passes:
    """Заход номер i пишет (растёт журнал отката), если writes[i] истинно."""

    def __init__(self, *writes: bool) -> None:
        self.writes = writes
        self.actions: list[str] = []

    def __call__(self, *, agent, workspace, action, config, approval_inbox=None):
        i = len(self.actions)
        self.actions.append(action.action)
        if i < len(self.writes) and self.writes[i]:
            agent.compensation_log.append(object())
        return CampaignActionOutcome(result="completed", llm_calls_spent=2, work_done=True)


def _run(tmp_path: Path, execute, **cfg):
    return run_campaign(
        CampaignConfig(goal=GOAL, max_cycles=14, max_idle_streak=3, dry_run=False,
                       max_unproductive_streak=0, pursue_goal_when_idle=True, **cfg),
        agent=SimpleNamespace(log=None, compensation_log=[]), workspace=str(tmp_path),
        gather_signals=_nothing_admissible, execute_action=execute,
        now_fn=lambda: datetime(2026, 9, 26, 12, 13, tzinfo=timezone.utc), sleep_fn=lambda _s: None,
    )


def test_reconnaissance_passes_do_not_close_the_goal(tmp_path: Path) -> None:
    execute = _Passes()
    result = _run(tmp_path, execute)
    assert execute.actions == [PURSUE_GOAL] * OPERATOR_GOAL_EMPTY_PASSES, execute.actions
    assert result.records[-1].result == "idle", "после трёх пустых заходов — стоп по простою"


def test_a_writing_pass_resets_the_count(tmp_path: Path) -> None:
    execute = _Passes(False, False, True)
    _run(tmp_path, execute)
    assert execute.actions == [PURSUE_GOAL] * (3 + OPERATOR_GOAL_EMPTY_PASSES), execute.actions


def test_the_agents_own_goal_keeps_one_pass(tmp_path: Path) -> None:
    execute = _Passes(True, True, True)
    _run(tmp_path, execute, goal_is_self=True)
    assert execute.actions == [PURSUE_GOAL], "своя цель агента сменяется драйвами, а не повторяется"
