"""Разные допустимые цели при одинаковых прочих сигналах дают разный выбор.

Замер, отвергнутые варианты и границы: MIR-158 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.best_next_action import resolve_goal_subject, select_best_next_action


#: Тот же двухшаговый путь, что и у боевого вызывающего: предмет разрешается
#: файловой системой снаружи, таблица решений остаётся чистой функцией.
def _EXISTS(rel: str) -> bool:
    return Path(rel).is_file()

#: Одна и та же живая запись дефекта, названная своим файлом. Прочие сигналы во
#: всех проверках ниже идентичны — меняется ТОЛЬКО цель.
_ISSUE = ({
    "title": "loop swallows a refusal",
    "status": "open",
    "occurrences": 3,
    "related_files": ["core/loop.py"],
    "evidence": ["detector fired three times"],
},)

#: Цели нарочно без слов «почини/раздели/refactor»: инженерная дверь не должна
#: открываться сама, иначе различие объяснялось бы ею, а не предметом.
_GOAL_SAME = "Проследить core/loop.py и описать его места вызова"
_GOAL_OTHER = "Проследить core/subagent_registry и описать его места вызова"


def _pick(goal: str, **overrides):
    kwargs = {
        "goal": goal,
        "goal_subject": resolve_goal_subject(goal, exists=_EXISTS),
        "result_status": "ok",
        "tests_health": "ok",
        "open_self_improvement_issues": _ISSUE,
        "self_improvement_registry_available": True,
    }
    kwargs.update(overrides)
    return select_best_next_action(**kwargs)


def test_a_goal_about_the_same_file_admits_the_defect_action() -> None:
    """Цель называет тот же файл — работа по дефекту остаётся допустимой."""
    assert _pick(_GOAL_SAME).action == "improve_failure_to_idea_pipeline"


def test_a_goal_about_another_file_does_not_get_that_action() -> None:
    """ДИФФЕРЕНЦИАЛЬНЫЙ СВИДЕТЕЛЬ: те же сигналы, другая цель — другой выбор.

    Живой прогон 2026-08-25: цель «Trace `core/subagent_registry` …» не дала НИ
    ОДНОГО кандидата (её предмет назван без `.py`, а извлечение требовало
    суффикса), и `max(priority)` отдал цикл единственному оставшемуся делу —
    чужому дефекту, ценой 39 единиц.
    """
    picked = _pick(_GOAL_OTHER)

    assert picked.action != "improve_failure_to_idea_pipeline", (
        "цель про core/subagent_registry всё ещё приводит к работе над "
        "core/loop.py — выбор действия не зависит от выбранной цели"
    )


def test_the_two_goals_really_differ_in_outcome() -> None:
    """Контроль различимости: без него обе проверки прошли бы на отказе во всём."""
    assert _pick(_GOAL_SAME).action != _pick(_GOAL_OTHER).action


def test_the_subject_is_resolved_against_the_workspace_not_a_suffix() -> None:
    """Предмет цели — существующий файл, а не совпадение с `.py`.

    Именно на суффиксе провалился живой прогон: `core/subagent_registry`
    существует, но не был узнан.
    """
    assert resolve_goal_subject(_GOAL_OTHER, exists=_EXISTS) == "core/subagent_registry.py"
    assert resolve_goal_subject(_GOAL_SAME, exists=_EXISTS) == "core/loop.py"
    assert resolve_goal_subject("Проследить core/no_such_module и описать", exists=_EXISTS) is None


@pytest.mark.parametrize("goal", [
    "",
    "Разобраться, почему агент повторяет одно и то же",
])
def test_a_goal_naming_no_artifact_constrains_nothing(goal: str) -> None:
    """Граница: без названного предмета связывать не по чему — поведение прежнее.

    Отвергнут вариант «нет предмета — запретить всё»: он превратил бы любую
    обобщённую цель в паралич, а обобщённая цель законна.
    """
    assert _pick(goal).action == "improve_failure_to_idea_pipeline"


def test_an_objective_breakage_is_never_filtered_out_by_a_goal() -> None:
    """Безопасность выше цели: сломанные тесты видны при любой цели.

    Иначе цель стала бы способом отвести взгляд от поломки — ровно то, чего
    нельзя допускать в ограничении по предмету.
    """
    picked = _pick(
        _GOAL_OTHER,
        result_status="failed",
        tests_health="fail",
        failed_tests=("tests/test_something.py::test_x",),
    )

    assert picked.severity in {"critical", "high"}
    assert picked.action != "improve_failure_to_idea_pipeline"


def test_the_idle_reason_names_the_real_cause_not_an_acknowledgement() -> None:
    """Отведённое по предмету никто не подтверждал.

    Первая версия сужения складывала отведённое туда же, куда заглушённое, и
    запасной вариант объявлял простой «подтверждённым оператором» — то есть
    называл оператору ложную причину. Основания разведены.
    """
    picked = _pick(_GOAL_OTHER)

    assert picked.action == "observe"
    assert "acknowledged by the operator" not in picked.reason
    assert "core/subagent_registry.py" in picked.reason
    assert picked.target_path == "core/subagent_registry.py"

