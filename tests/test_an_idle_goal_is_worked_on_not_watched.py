"""A goal the menu cannot bind is worked on, not watched — when the run asks for it.

24h run 2026-09-19, 18:05–18:32: 27 cycles, 9 «nothing to do» although each
goal was concrete and self-chosen («read core/best_next_action.py and record
which lines implement goal-repeat»). The goal filtered a menu of actions; when
no action named its subject the cycle idled. And every agent pass the campaign
did run was told «decide the ONE step a human should take … do not perform any
effects» — so the work came back as a note to the operator.

With `pursue_goal_when_idle` the idle cycle becomes one agent pass on the goal
itself (once per goal), told to do the work. Off by default.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.best_next_action import BestNextAction
from core.campaign import PURSUE_GOAL, CampaignActionOutcome, CampaignConfig, run_campaign
from core.campaign_io import _action_focused_goal

GOAL = "Read core/best_next_action.py and record which lines implement goal-repeat"


class _NothingAdmissible:
    def __call__(self, agent, workspace, approval_inbox, goal="", exhausted_actions=frozenset()):
        return {"action": BestNextAction(
            action="observe", title="Observe", severity="none", priority=0,
            reason="Nothing admissible for the chosen goal: 5 naming no subject at all.",
            decided_by="no_candidate", grounds="operator_goal")}


class _Record:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def __call__(self, *, agent, workspace, action, config, approval_inbox=None):
        self.actions.append(action.action)
        return CampaignActionOutcome(result="completed", llm_calls_spent=2, work_done=True)


def _run(tmp_path: Path, execute, **cfg):
    return run_campaign(
        CampaignConfig(goal=GOAL, max_cycles=6, max_idle_streak=3, dry_run=False,
                       max_unproductive_streak=0, **cfg),
        agent=SimpleNamespace(log=None), workspace=str(tmp_path),
        gather_signals=_NothingAdmissible(), execute_action=execute,
        now_fn=lambda: datetime(2026, 9, 19, 15, 22, tzinfo=timezone.utc), sleep_fn=lambda _s: None,
    )


def test_the_goal_itself_is_worked_on_once(tmp_path: Path) -> None:
    execute = _Record()
    result = _run(tmp_path, execute, pursue_goal_when_idle=True)
    assert execute.actions == [PURSUE_GOAL], execute.actions
    assert result.records[0].result == "completed"


def test_default_behaviour_is_unchanged(tmp_path: Path) -> None:
    execute = _Record()
    result = _run(tmp_path, execute)
    assert execute.actions == []
    assert result.records[0].result == "idle"


def test_the_pass_is_told_to_do_the_work_not_advise_a_human() -> None:
    action = BestNextAction(action=PURSUE_GOAL, title="t", severity="medium", priority=1, reason="r")
    prompt = _action_focused_goal(GOAL, action)
    assert GOAL in prompt and "Do the work yourself" in prompt
    assert "a human should take" not in prompt and "do not perform any effects" not in prompt


def test_goal_pick_attempts_are_a_setting(tmp_path: Path, monkeypatch) -> None:
    """Перезапуск 2026-09-19 18:36: три попытки выбрать цель отверг страж
    новизны, прогон остановился. Число попыток — `AGENT_GOAL_PICK_ATTEMPTS`
    (умолчание 3, потолок 20), одно на старт и на смену цели."""
    from core.campaign import goal_pick_attempts

    monkeypatch.delenv("AGENT_GOAL_PICK_ATTEMPTS", raising=False)
    assert goal_pick_attempts() == 3
    monkeypatch.setenv("AGENT_GOAL_PICK_ATTEMPTS", "99")
    assert goal_pick_attempts() == 20
    monkeypatch.setenv("AGENT_GOAL_PICK_ATTEMPTS", "7")
    asked: list[int] = []

    def refuse() -> str:  # always proposes the goal it already has — never accepted
        asked.append(1)
        return GOAL

    run_campaign(
        CampaignConfig(goal=GOAL, max_cycles=4, max_idle_streak=3, dry_run=False, max_unproductive_streak=0),
        agent=SimpleNamespace(log=None), workspace=str(tmp_path),
        gather_signals=_NothingAdmissible(), execute_action=_Record(), next_goal=refuse,
        now_fn=lambda: datetime(2026, 9, 19, 15, 36, tzinfo=timezone.utc), sleep_fn=lambda _s: None,
    )
    assert len(asked) >= 7 and len(asked) % 7 == 0, len(asked)


class _SelfRepairWins:
    """The menu as measured on 2026-09-19: a retained record of an own failure
    outranks the goal (301 of 471 cycles, 15/207 and 0/77 done)."""

    def __init__(self, severity: str = "medium") -> None:
        self.severity = severity

    def __call__(self, agent, workspace, approval_inbox, goal="", exhausted_actions=frozenset()):
        return {"action": BestNextAction(
            action="improve_failure_to_idea_pipeline", title="t", severity=self.severity, priority=59,
            reason="Recent self-improvement history contains a rollback lesson",
            decided_by="priority_table", grounds="retained_record")}


def _goal_first_run(tmp_path: Path, gather, next_goal=None):
    execute = _Record()
    result = run_campaign(
        CampaignConfig(goal=GOAL, max_cycles=4, max_idle_streak=1, dry_run=False,
                       max_unproductive_streak=0, goal_first=True),
        agent=SimpleNamespace(log=None), workspace=str(tmp_path),
        gather_signals=gather, execute_action=execute, next_goal=next_goal,
        now_fn=lambda: datetime(2026, 9, 19, 19, 30, tzinfo=timezone.utc), sleep_fn=lambda _s: None,
    )
    return execute, result


def test_goal_first_the_goal_is_done_not_the_menu(tmp_path: Path) -> None:
    goals = iter(["Второй вопрос по книге", "Третий вопрос по книге", ""])
    execute, result = _goal_first_run(tmp_path, _SelfRepairWins(), next_goal=lambda: next(goals))
    assert "improve_failure_to_idea_pipeline" not in execute.actions
    assert execute.actions[:2] == [PURSUE_GOAL, PURSUE_GOAL], execute.actions
    assert result.records[1].result == "idle", "after its pass the goal is replaced, not re-done"


def test_goal_first_a_real_breakage_still_wins(tmp_path: Path) -> None:
    execute, _ = _goal_first_run(tmp_path, _SelfRepairWins(severity="high"))
    assert execute.actions[0] == "improve_failure_to_idea_pipeline"
