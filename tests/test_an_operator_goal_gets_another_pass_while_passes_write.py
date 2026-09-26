"""An operator's goal gets another pass while the previous pass wrote something.

Live run 26.09: the goal «connect five local models, each with a witness test» got
ONE agent pass; then three idle cycles and healthy_idle after 4 minutes. A pass
that wrote nothing still ends the goal's turn — the idle stop stays.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.best_next_action import BestNextAction
from core.campaign import PURSUE_GOAL, CampaignActionOutcome, CampaignConfig, run_campaign

GOAL = "Подключи к себе проверенные локальные модели как инструменты, каждую с тестом"


def _nothing_admissible(agent, workspace, approval_inbox, goal="", exhausted_actions=frozenset()):
    return {"action": BestNextAction(
        action="observe", title="Observe", severity="none", priority=0,
        reason="Nothing admissible for the chosen goal.", decided_by="no_candidate", grounds="operator_goal")}


class _WritesFirst:
    """Первые `writes` заходов что-то записывают (растёт журнал отката), дальше — нет."""

    def __init__(self, writes: int) -> None:
        self.writes = writes
        self.actions: list[str] = []

    def __call__(self, *, agent, workspace, action, config, approval_inbox=None):
        self.actions.append(action.action)
        if len(self.actions) <= self.writes:
            agent.compensation_log.append(object())
        return CampaignActionOutcome(result="completed", llm_calls_spent=2, work_done=True)


def _run(tmp_path: Path, execute, **cfg):
    return run_campaign(
        CampaignConfig(goal=GOAL, max_cycles=10, max_idle_streak=3, dry_run=False,
                       max_unproductive_streak=0, pursue_goal_when_idle=True, **cfg),
        agent=SimpleNamespace(log=None, compensation_log=[]), workspace=str(tmp_path),
        gather_signals=_nothing_admissible, execute_action=execute,
        now_fn=lambda: datetime(2026, 9, 26, 11, 30, tzinfo=timezone.utc), sleep_fn=lambda _s: None,
    )


def test_passes_continue_while_they_write(tmp_path: Path) -> None:
    execute = _WritesFirst(writes=2)
    result = _run(tmp_path, execute)
    assert execute.actions == [PURSUE_GOAL] * 3, execute.actions
    assert result.records[-1].result == "idle", "пустой заход закрывает очередь, стоп по простою"


def test_a_pass_that_writes_nothing_is_the_last(tmp_path: Path) -> None:
    execute = _WritesFirst(writes=0)
    _run(tmp_path, execute)
    assert execute.actions == [PURSUE_GOAL]


def test_the_agents_own_goal_keeps_one_pass(tmp_path: Path) -> None:
    execute = _WritesFirst(writes=5)
    _run(tmp_path, execute, goal_is_self=True)
    assert execute.actions == [PURSUE_GOAL], "своя цель агента сменяется драйвами, а не повторяется"
