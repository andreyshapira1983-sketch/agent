"""Направление причины решается прогоном, а не рассуждением.

Живой случай 2026-09-20, вечер. Агент сам поставил себе задачу про
собственный код: «почему при `pursue_goal` с goal-first логикой пайплайн
`improve_failure_to_idea_pipeline` не срабатывает». Он нашёл нужные места,
прочитал их и выписал ВСЕ факты верно:

  * `_goal_first` определена в core/campaign.py:180;
  * её первая проверка возвращает действие без изменений при
    `severity in ("critical", "high")`;
  * `improve_failure_to_idea_pipeline` рождается с `severity="medium"`.

А вывод сложил наизнанку: «не срабатывает ИЗ-ЗА раннего возврата при
critical/high» — хотя по его же факту у пайплайна severity равен medium,
то есть до этого возврата дело не доходит. Истина обратная: пайплайн
вытесняется целью ПОТОМУ ЧТО он НЕ критичен.

Дешёвого детектора для такой ошибки нет, и это измерено: правило «вывод
называет условие, которое факты отрицают» дало 0 срабатываний на 194
ответах с разделами Conclusion и Facts — оно не ловит даже этот случай,
потому что рассуждение и факт здесь не противоречат друг другу дословно.
Направление причины лексикой не устанавливается.

Зато оно устанавливается ИСПОЛНЕНИЕМ, и для этого у подъёма есть орган:
двурукавный эксперимент, где причина доказывается её устранением. До сих
пор он мог исполнить ровно одну функцию (`reasoning_action_check`), и
почти ничего в себе агент проверить не мог — только рассуждать. Здесь
добавлена вторая цель: `_goal_first` чиста (строит объекты, ничего не
читает и не пишет), и именно она решает спорный вопрос за один прогон.
"""
from __future__ import annotations

from core.causal_climb_action import (
    _EXPERIMENT_TARGETS,
    parse_experiment,
    spec_is_executable,
    target_contracts,
)

#: Рукав A несёт причину (medium — не критично), рукав B её устраняет.
_SPEC = ("[exp: goal_first | "
         "A=severity=medium ;; action=improve_failure_to_idea_pipeline | "
         "B=severity=high ;; action=improve_failure_to_idea_pipeline | "
         "след=action=pursue_goal]")


def test_the_target_is_registered_with_its_contract() -> None:
    assert "goal_first" in _EXPERIMENT_TARGETS
    contract = target_contracts()
    assert "goal_first" in contract and "severity=" in contract


def test_the_arms_disagree_and_the_effect_names_the_cause() -> None:
    spec = parse_experiment(_SPEC)
    assert spec is not None
    assert spec_is_executable(spec) == "", "спека обязана исполняться"

    run = _EXPERIMENT_TARGETS[spec.target]
    out_a, out_b = run(spec.arm_a), run(spec.arm_b)
    # Следствие есть в A и исчезает в B — значит причина названа верно:
    # вытесняет цель именно НЕ-критичность, а не ранний возврат.
    assert spec.effect in out_a, out_a
    assert spec.effect not in out_b, out_b


def test_the_inverted_claim_is_refuted_by_the_same_run() -> None:
    """Вывод агента: «из-за раннего возврата при critical/high»."""
    run = _EXPERIMENT_TARGETS["goal_first"]
    critical = run("severity=critical ;; action=improve_failure_to_idea_pipeline")
    assert "action=improve_failure_to_idea_pipeline" in critical, (
        "при critical действие ОСТАЁТСЯ — значит ранний возврат его не губит"
    )


def test_a_goal_with_its_own_action_wins_over_the_menu() -> None:
    run = _EXPERIMENT_TARGETS["goal_first"]
    out = run("severity=medium ;; action=observe ;; goal_action=explain_causal_observation")
    assert "action=explain_causal_observation" in out
