"""Разные допустимые цели при одинаковых прочих сигналах дают разный выбор.

Замер, отвергнутые варианты и границы: MIR-158 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from pathlib import Path

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


def test_the_two_goals_really_differ_in_outcome() -> None:
    """Контроль различимости: без него обе проверки прошли бы на отказе во всём."""
    assert _pick(_GOAL_SAME).action != _pick(_GOAL_OTHER).action
