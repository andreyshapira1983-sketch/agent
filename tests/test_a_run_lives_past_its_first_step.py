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

from core.best_next_action import BestNextAction, select_best_next_action

# ── L2 ──────────────────────────────────────────────────────────────────────


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
