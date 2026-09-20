"""Сводка не приписывает человеку цели, которых он не ставил.

Живой разговор 2026-09-21. Оператор сказал: «у него всё есть, он может менять
цели сам». Читатель журналов возразил, что цель поставлена человеком, — и
оказался неправ: в `logs/campaign_goal_source.txt` стоит одно слово `agent`,
а ключа `--goal` в запуске нет вовсе. Цель «разбить core/step_sanitizer.py»
агент выбрал себе сам.

Обмануло обоих одно: `grounds` у ВСЯКОЙ цели равнялось `operator_goal`, кто
бы её ни выбрал. Отсюда сводка «117 решений из 117 по операторской цели» —
при том, что оператор не ставил ни одной.

Метка оказалась не косметической, и нашёл это сам агент, задав единственный
за вечер вопрос: «а читается ли поле обратно?». Читается, в двух местах:

  * `_partition_by_subject` освобождает по нему кандидата от сужения по
    предмету — то есть собственная цель агента получала защиту,
    предназначенную слову человека, и каждый цикл побеждала его же дефекты;
  * `core/campaign.py` считает по нему `goal_drove_cycles` — счётчик,
    которым агент отчитывается перед человеком о том, кто им правил.

Здесь чинится ПРАВДА, а не поведение. Освобождение от сужения оставлено
обеим целям нарочно: отнимать ли его у самовыбранной — отдельное решение, и
протаскивать его молча, вместе с переименованием, значило бы спрятать от
оператора именно то, что он просил показать.
"""
from __future__ import annotations

from core.best_next_action import (
    GOAL_GROUNDS,
    BestNextAction,
    _partition_by_subject,
    select_best_next_action,
)
from core.campaign import _goal_first

_GOAL = "Разбить свой модуль core/step_sanitizer.py на части"


def _grounds_for(goal_is_self: bool) -> str:
    action = select_best_next_action(goal=_GOAL, goal_is_self=goal_is_self)
    return action.grounds


def test_a_goal_the_agent_chose_says_so() -> None:
    assert _grounds_for(True) == "self_goal"


def test_a_goal_the_human_named_says_so() -> None:
    assert _grounds_for(False) == "operator_goal"


def test_goal_first_carries_the_same_truth() -> None:
    """Режим «цель первой» метит происхождение так же, как меню."""
    idle = BestNextAction(action="observe", title="—", severity="none",
                          priority=0, reason="—")
    mine = _goal_first(idle, set(), "propose_engineering_task", goal_is_self=True)
    theirs = _goal_first(idle, set(), "propose_engineering_task", goal_is_self=False)
    assert mine.grounds == "self_goal"
    assert theirs.grounds == "operator_goal"


def test_both_kinds_count_as_the_goal_driving_the_work() -> None:
    """Счётчик сводки про то, вела ли работу ЦЕЛЬ, а не про то, чья она."""
    assert {"operator_goal", "self_goal"} == set(GOAL_GROUNDS)


def test_the_exemption_is_unchanged_for_both() -> None:
    """Поведение оставлено прежним нарочно: чинится отчёт, а не отбор."""
    def _cand(grounds: str) -> BestNextAction:
        return BestNextAction(action="propose_engineering_task", title="—",
                              severity="medium", priority=1, reason="—",
                              target_path="core/other.py", grounds=grounds)

    on, off, unknown = _partition_by_subject(
        [_cand("operator_goal"), _cand("self_goal")], "core/step_sanitizer.py")
    assert len(on) == 2, (len(on), len(off), len(unknown))
