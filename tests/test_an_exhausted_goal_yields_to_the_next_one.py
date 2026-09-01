"""Исчерпанная цель уступает следующей — прогон продолжается, а не умирает.

Замер 2026-09-01, из-за которого право появилось: агент сам выбрал узкую
инженерную цель («предложить расщепление core/smart_memory.py»), сделал по ней
работу в ПЕРВОМ цикле — предложение произведено и ушло ждать человека, — а
дальше делать по этой цели было нечего. Он снова выбирал то же действие над
тем же предметом, предметный страж честно считал повторы, и на третьем датчик
застоя гасил кампанию: четыре минуты вместо десяти часов. С широкой целью тот
же агент в тот же день отработал 21 полезный цикл из 21.

Не хватало не полномочий и не бюджета — их подняли, и это не помогло. Не
хватало права сказать себе «это дело кончилось, беру следующее».

Границы права, и они проверяются здесь: смена идёт через ТЕ ЖЕ ворота, что и
цель на старте (её выбирает агент, и отказ хартии останавливает прогон, а не
обходится); память о повторах прежней темы сбрасывается, иначе первый же шаг
по новой теме объявили бы повтором и право оказалось бы фиктивным; число смен
за прогон ограничено — слишком много признаний «дело кончилось» честнее
закончить прогоном, чем перебором тем.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from core.campaign import run_campaign
from core.campaign_ledger import CampaignLedger
from core.campaign_types import CampaignActionOutcome, CampaignConfig


class _OneActionGather:
    """Всегда предлагает одно и то же действие над одним предметом."""

    def __init__(self, action: str = "propose_engineering_task"):
        self.action = action
        self.goals_seen: list[str] = []

    def __call__(self, agent, workspace, approval_inbox, goal="",
                 exhausted_actions=frozenset()):
        self.goals_seen.append(goal)
        return {"action": SimpleNamespace(
            action=self.action, title="one thing", severity="medium",
            priority=59, risk="reversible", grounds="operator_goal",
            decided_by="test", next_check_at=None, reason="",
        )}


class _WorksOnce:
    """Первый цикл делает работу, дальше предмет тот же — пойдут повторы."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *, agent, workspace, action, config, approval_inbox=None):
        self.calls += 1
        return CampaignActionOutcome(
            result="completed", llm_calls_spent=1, cost_units_spent=1,
            subject="the-only-subject", work_done=True,
        )


def _run(tmp_path, gather, execute, next_goal=None, max_cycles=12):
    return run_campaign(
        CampaignConfig(goal="первая цель", max_cycles=max_cycles,
                       max_idle_streak=3, dry_run=False),
        agent=SimpleNamespace(log=None),
        workspace=str(tmp_path),
        gather_signals=gather,
        execute_action=execute,
        ledger=CampaignLedger(),
        next_goal=next_goal,
        now_fn=lambda: datetime(2026, 9, 1, 21, 0, tzinfo=timezone.utc),
    )


def test_without_the_right_the_run_dies_on_the_stall(tmp_path):
    """Прогон A: как было до права — застой гасит кампанию."""
    result = _run(tmp_path, _OneActionGather(), _WorksOnce())

    assert result.status == "stopped"
    assert "no_progress_stall" in result.stop_reason


def test_an_exhausted_goal_is_replaced_and_the_run_continues(tmp_path):
    """Прогон B: то же самое, но с правом — цель меняется, работа идёт."""
    gather = _OneActionGather()
    goals = iter(["вторая цель", "третья цель"])

    result = _run(tmp_path, gather, _WorksOnce(),
                  next_goal=lambda: next(goals, ""))

    assert "вторая цель" in gather.goals_seen, "новая цель обязана дойти до выбора действия"
    assert "третья цель" in gather.goals_seen, "исчерпание повторяется — цель меняется снова"
    switched = [r for r in result.records if r.goal == "вторая цель"]
    assert switched, "циклы после смены записываются под НОВОЙ целью"
    worked_after_switch = [r for r in switched if r.result == "completed"]
    assert worked_after_switch, "после смены цели снова идёт РАБОТА, а не только повторы"
    assert len(result.records) > 4, (
        "без права прогон умирал на четвёртом цикле; с правом он продолжается"
    )


def test_a_refused_next_goal_stops_the_run_honestly(tmp_path):
    """Ворота те же: хартия отказала — прогон заканчивается, а не обходит её."""
    result = _run(tmp_path, _OneActionGather(), _WorksOnce(),
                  next_goal=lambda: "")

    assert result.status == "stopped"
    assert "no_progress_stall" in result.stop_reason


def test_a_failing_goal_picker_never_kills_the_run(tmp_path):
    """Смена цели — удобство, а не несущая конструкция: её падение молчаливо."""
    def _explodes() -> str:
        raise RuntimeError("модель недоступна")

    result = _run(tmp_path, _OneActionGather(), _WorksOnce(), next_goal=_explodes)

    assert result.status == "stopped"
    assert "no_progress_stall" in result.stop_reason


def test_the_same_goal_offered_again_is_not_a_switch(tmp_path):
    """Та же цель — не смена: иначе застой обходился бы по кругу навсегда."""
    result = _run(tmp_path, _OneActionGather(), _WorksOnce(),
                  next_goal=lambda: "первая цель")

    assert result.status == "stopped"
    assert "no_progress_stall" in result.stop_reason
