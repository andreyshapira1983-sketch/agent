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


_OWN_DEFECT = {
    "id": "sii_own",
    "fingerprint": "sii_f49e4ac9824ce89a",
    "title": "Goal-reply parser takes the first '{' to the last '}' so braces in prose break a valid final JSON",
    "status": "open",
    "action": "parse_the_last_complete_json_object_of_a_reply",
    "severity": "medium",
    "related_files": ["core/charter_goal.py"],
    "evidence": ["witness: tests/test_a_silent_thinker_is_told_apart_from_a_bad_parser.py"],
}

_REPAIR_GOAL = (
    "Repair goal-reply parser: run witness case (c) to red, then make "
    "prose-with-braces no longer break extraction of final JSON object; add "
    "regression test"
)


def test_a_goal_that_names_an_own_defect_gets_that_defect_as_hands():
    """Положительная сторона Д3 (замер 2026-09-03 07:34: цель — ремонт парсера,
    руки — чужое дело про встречный вопрос). Цель, называющая собственный
    дефект словами его записи, обязана получить действие ЭТОГО дефекта, с
    основанием в цели."""
    picked = select_best_next_action(
        goal=_REPAIR_GOAL, self_improvement_registry_available=True,
        open_self_improvement_issues=(_STRANGER, _OWN_DEFECT),
    )

    assert picked.action == "parse_the_last_complete_json_object_of_a_reply", (
        f"цель про парсер, а руки: {picked.action!r} ({picked.grounds}, {picked.decided_by})"
    )
    assert picked.grounds == "operator_goal"
    assert picked.target_path == "core/charter_goal.py"


def test_a_broad_self_improvement_goal_keeps_the_habit():
    """Граница отрицательной стороны: ОБЩАЯ цель («улучшай себя») привычку
    реестра не отводит — это её законное чтение (тесты 2026-08-26 про
    «найди дефект и почини»). Отводит только цель, назвавшая другую запись
    или конкретную вещь, которой запись не знает."""
    picked = select_best_next_action(
        goal="Improve yourself in whatever way the measured evidence supports",
        self_improvement_registry_available=True,
        open_self_improvement_issues=(_STRANGER,),
        unexplained_observations_count=3,
    )

    assert picked.action == "carry_observed_values_between_plan_steps"
    assert picked.grounds == "retained_record"


def test_without_a_goal_the_habit_still_runs():
    """Граница: без цели реестр дефектов по-прежнему единственный законный
    источник дела (test_the_unattended_path_sees_its_own_defects)."""
    picked = select_best_next_action(
        goal="", self_improvement_registry_available=True,
        open_self_improvement_issues=(_STRANGER,),
    )

    assert picked.action == "carry_observed_values_between_plan_steps"


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


def test_an_investigation_goal_is_not_handed_to_an_unrelated_record():
    """Был красным свидетелем 2026-09-03 (banked strict-xfail), зелёный после
    минимального ремонта тем же днём по слову оператора: под явной целью
    привычка реестра допускается к гонке только если цель назвала её предмет
    или саму запись; иначе — честный простой, называющий цель."""
    picked = select_best_next_action(
        goal=_GOAL, self_improvement_registry_available=True,
        open_self_improvement_issues=(_STRANGER,),
    )

    assert picked.grounds == "operator_goal" or picked.action == "observe", (
        f"цель просит расследование, а цикл получил {picked.action!r} "
        f"(grounds={picked.grounds!r}, decided_by={picked.decided_by!r}) — "
        "чужое дело под чужой целью: замер 2026-09-03, cycle 1"
    )
