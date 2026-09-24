"""Прогон живёт дольше первого шага — шесть швов живучести (блок 2).

Аудит 2026-09-03 (AUTONOMY_AUDIT_2026-09-03.md in git history, раздел 2). Профиль
каждого прогона с собственной целью с 01.09: один полезный цикл → три
повтора → стоп, четыре минуты. Причины — структурные, не модельные:

  L1 после смены цели исполнителю уходит ЗАМОРОЖЕННАЯ стартовая цель
     (config.goal): смена меняла, кто выбирает, а не над чем работают;
  L2 «исчерпано» умеют сообщать только четыре причинных действия; прочие
     переизбираются вечно, и первая же запись реестра закрывает остальные;
  L3 мост «диагноз → заявка на ремонт» открыт одному имени действия,
     которого нет ни у одного его дела;
  L4 сигнал «свежий провал» (приоритет 60) недостижим при непустом реестре;
  L5 смена цели достижима только из выхода REPEAT — простой (healthy_idle)
     завершает прогон без попытки;
  L6 на смене одна попытка против трёх на старте.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from core.best_next_action import BestNextAction, select_best_next_action
from core.campaign import run_campaign
from core.campaign_ledger import CampaignLedger
from core.campaign_types import CampaignActionOutcome, CampaignConfig


def _action(name: str, priority: int = 59) -> SimpleNamespace:
    return SimpleNamespace(
        action=name, title=name, severity="medium", priority=priority,
        risk="reversible", grounds="operator_goal", decided_by="test",
        next_check_at=None, reason="", evidence=(), target_path=None,
    )


class _Gather:
    """Возвращает действие по цели; запоминает, что ему передали."""

    def __init__(self, by_goal):
        self.by_goal = by_goal
        self.calls: list[dict] = []

    def __call__(self, agent, workspace, approval_inbox, goal="", exhausted_actions=frozenset()):
        self.calls.append({"goal": goal, "exhausted": frozenset(exhausted_actions or ())})
        return {"action": self.by_goal(goal)}


class _Execute:
    def __init__(self):
        self.goals_seen: list[str] = []

    def __call__(self, *, agent, workspace, action, config, approval_inbox=None):
        self.goals_seen.append(config.goal)
        return CampaignActionOutcome(result="completed", llm_calls_spent=1,
                                     cost_units_spent=1, subject="s", work_done=True)


def _run(tmp_path, gather, execute, next_goal=None, max_cycles=12):
    return run_campaign(
        CampaignConfig(goal="первая цель", max_cycles=max_cycles, max_idle_streak=3, dry_run=False),
        agent=SimpleNamespace(log=None), workspace=str(tmp_path),
        gather_signals=gather, execute_action=execute, ledger=CampaignLedger(),
        next_goal=next_goal,
        now_fn=lambda: datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
    )


# ── L1 ──────────────────────────────────────────────────────────────────────

def test_after_a_switch_the_worker_hears_the_current_goal(tmp_path):
    goals = iter(["вторая цель"])
    gather = _Gather(lambda g: _action("one_thing"))
    execute = _Execute()

    _run(tmp_path, gather, execute, next_goal=lambda: next(goals, ""))

    assert "вторая цель" in execute.goals_seen, (
        f"исполнитель так и не услышал новую цель: {execute.goals_seen}"
    )


# ── L2 ──────────────────────────────────────────────────────────────────────

def test_a_repeated_bare_action_is_reported_exhausted_to_the_gatherer(tmp_path):
    gather = _Gather(lambda g: _action("investigate_x"))

    _run(tmp_path, gather, _Execute(), max_cycles=4)

    assert any("investigate_x" in c["exhausted"] for c in gather.calls), (
        "после повтора действие обязано ехать сборщику как исчерпанное"
    )


_ISSUE_A = {"fingerprint": "sii_a", "title": "first defect", "status": "open",
            "action": "act_first", "related_files": ["core/a.py"], "evidence": ["e"]}
_ISSUE_B = {"fingerprint": "sii_b", "title": "second defect", "status": "open",
            "action": "act_second", "related_files": ["core/b.py"], "evidence": ["e"]}


def test_an_exhausted_first_issue_yields_to_the_second():
    picked = select_best_next_action(
        goal="", self_improvement_registry_available=True,
        open_self_improvement_issues=(_ISSUE_A, _ISSUE_B),
        exhausted_actions=frozenset({"act_first"}),
    )

    assert picked.action == "act_second", f"второе дело недостижимо: {picked.action!r}"


def test_the_first_issue_still_wins_when_nothing_is_exhausted():
    """Положительный контроль L2."""
    picked = select_best_next_action(
        goal="", self_improvement_registry_available=True,
        open_self_improvement_issues=(_ISSUE_A, _ISSUE_B),
    )

    assert picked.action == "act_first"


# ── L3 ──────────────────────────────────────────────────────────────────────

def test_an_issue_derived_action_is_eligible_for_the_repair_bridge():
    from core.campaign_io import _is_own_issue_action

    own = BestNextAction(
        action="reach_the_reasoning_roster_in_live_runs", title="t", severity="medium",
        priority=59, reason="r",
        evidence=("goal names durable issue sii_a06d", "core/llm.py: _roster_path returns None"),
    )
    habit = BestNextAction(
        action="improve_failure_to_idea_pipeline", title="t", severity="medium",
        priority=55, reason="r", evidence=("durable issue sii_x status=open seen>=1x",),
    )
    probe = BestNextAction(action="restore_daemon_liveness", title="t", severity="high",
                           priority=100, reason="r", evidence=("heartbeat missing",))

    assert _is_own_issue_action(own) is True
    assert _is_own_issue_action(habit) is True
    assert _is_own_issue_action(probe) is False


# ── L4 ──────────────────────────────────────────────────────────────────────

def test_a_fresh_failure_outranks_the_registry_habit():
    picked = select_best_next_action(
        goal="", self_improvement_registry_available=True,
        open_self_improvement_issues=(_ISSUE_A,),
        recent_self_improvement_failures=("self-split rolled back: full pytest failed",),
        fresh_self_improvement_failure=True,
    )

    assert picked.priority == 60, f"свежий провал недостижим: {picked.action!r} @ {picked.priority}"
    assert picked.action == "improve_failure_to_idea_pipeline"


# ── L5 ──────────────────────────────────────────────────────────────────────

def _observe() -> SimpleNamespace:
    return SimpleNamespace(
        action="observe", title="nothing", severity="none", priority=0, risk="read_only",
        grounds="observed_state", decided_by="no_candidate", next_check_at=None,
        reason="nothing admissible", evidence=(), target_path=None,
    )


def test_an_idle_goal_is_switched_not_declared_healthy(tmp_path):
    goals = iter(["вторая цель"])
    gather = _Gather(lambda g: _observe() if g == "первая цель" else _action("real_work"))
    execute = _Execute()

    result = _run(tmp_path, gather, execute, next_goal=lambda: next(goals, ""))

    assert "вторая цель" in execute.goals_seen, (
        f"простой по первой цели должен вести к смене, а не к healthy_idle: {result.stop_reason}"
    )


# ── L6 ──────────────────────────────────────────────────────────────────────

def test_the_switch_gets_three_attempts_like_the_start(tmp_path):
    goals = iter(["", "", "вторая цель"])
    gather = _Gather(lambda g: _action("one_thing"))
    execute = _Execute()

    _run(tmp_path, gather, execute, next_goal=lambda: next(goals, ""))

    assert "вторая цель" in execute.goals_seen, "две неудачные попытки не должны убивать прогон"
