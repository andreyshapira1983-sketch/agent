"""Цель «расследовать» не отдаёт цикл чужому делу из реестра.

Замер 2026-09-03, прогон charter_day4_reasoner: думающий планировщик выбрал
точную цель — «Investigate why episode ep-run-…fc8125 was recorded as success
1.0 despite the producer's refusal (no_grounded_target) leaving no trace…».
Руки исполнили действие `carry_observed_values_between_plan_steps` — открытый
дефект про перенос значений между шагами, к эпизоду не имеющий отношения.
Леджер: grounds=retained_record, decided_by=sole_candidate, priority=55.

Дискриминатор — какая из двух причин: (1) генераторы кандидатов ПО ЦЕЛИ не
узнают класс «расследовать/проследить» и дают ноль кандидатов, и привычка
реестра остаётся единственным бегуном; или (2) кандидат по цели есть, но
проигрывает гонку приоритетов привычке. Второй свидетель ниже это различает:
без открытых дефектов та же цель уходит в простой `observe` с
decided_by=no_candidate — кандидатов по цели НОЛЬ. Причина (1).

Контракт (доктрина самого селектора: «цель СУЖАЕТ допустимое»): под явной
целью действие либо основано на ней (grounds=operator_goal), либо селектор
честно простаивает, называя цель, — но не отдаёт цикл чужому делу.
"""
from __future__ import annotations

import pytest

from core.best_next_action import select_best_next_action

_GOAL = (
    "Investigate why episode ep-run-run_fc8125c831d0aa3244260de997d22e4c was "
    "recorded as success 1.0 despite the producer's refusal (no_grounded_target) "
    "leaving no trace; draft a reviewed hypothesis and one concrete detection "
    "rule to catch such fact loss."
)

_STRANGER = {
    "id": "sii_stranger",
    "title": "План не проносит вывод инструмента A в аргументы инструмента B",
    "status": "open",
    "action": "carry_observed_values_between_plan_steps",
    "severity": "medium",
    "related_files": ["core/loop_step_execution.py"],
    "signature": "carry_observed_values_between_plan_steps",
}


def test_an_investigation_goal_yields_no_goal_grounded_candidate_today():
    """Зелёный дискриминатор причины: без чужих дел цель даёт ПУСТУЮ гонку."""
    picked = select_best_next_action(
        goal=_GOAL, self_improvement_registry_available=True,
        open_self_improvement_issues=(),
    )

    assert picked.action == "observe"
    assert picked.decided_by == "no_candidate", (
        "если здесь появился кандидат по цели — причина сместилась в гонку "
        "приоритетов, и красный свидетель ниже читается иначе"
    )


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, measured 2026-09-03 and banked rather than fixed (RED witness "
        "by the operator's word): the goal-derived candidate generators know only "
        "three goal classes (engineering task, doctrine document, external study); "
        "an 'Investigate/Trace why …' goal yields ZERO candidates (green test above, "
        "decided_by=no_candidate), so the durable-issue habit at priority 55 runs "
        "alone and the cycle goes to an unrelated open defect (charter_day4_reasoner "
        "cycle 1: carry_observed_values_between_plan_steps under an episode-audit "
        "goal). Cause (1) — a missing generator — not a lost priority race. Minimal "
        "repair proposed, not applied: a fourth goal-grounded generator for the "
        "investigate/trace class routing to the causal-climb organ "
        "(explain_causal_observation) with the goal as subject, and — as a guard — "
        "under an explicit goal a retained_record candidate wins only when its "
        "subject matches; otherwise honest idle naming the goal. "
        "[until: 2026-09-30 — перемерь закреплённую дыру; чини или пере-датируй явным коммитом]"
    ),
    strict=True,
)
def test_an_investigation_goal_is_not_handed_to_an_unrelated_record():
    """Красный свидетель: с чужим открытым дефектом в реестре цикл уходит ему."""
    picked = select_best_next_action(
        goal=_GOAL, self_improvement_registry_available=True,
        open_self_improvement_issues=(_STRANGER,),
    )

    assert picked.grounds == "operator_goal" or picked.action == "observe", (
        f"цель просит расследование, а цикл получил {picked.action!r} "
        f"(grounds={picked.grounds!r}, decided_by={picked.decided_by!r}) — "
        "чужое дело под чужой целью: замер 2026-09-03, cycle 1"
    )
