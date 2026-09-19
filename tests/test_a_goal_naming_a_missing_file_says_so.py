"""Цель, назвавшая несуществующий файл, отказывает НАЗВАННО, а не молча теряет силу.

Замер, отвергнутые варианты и границы: MIR-161 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.best_next_action import (
    resolve_goal_subject,
    select_best_next_action,
    unresolved_goal_targets,
)


def _exists(rel: str) -> bool:
    return Path(rel).is_file()


_ISSUE = ({
    "title": "loop swallows a refusal",
    "status": "open",
    "occurrences": 3,
    "related_files": ["core/loop.py"],
    "evidence": ["detector fired three times"],
},)


def _pick(goal: str):
    return select_best_next_action(
        goal=goal,
        goal_subject=resolve_goal_subject(goal, exists=_exists),
        goal_names_missing=unresolved_goal_targets(goal, exists=_exists),
        result_status="ok",
        tests_health="ok",
        open_self_improvement_issues=_ISSUE,
        self_improvement_registry_available=True,
    )


def test_a_missing_file_is_told_apart_from_no_file_at_all() -> None:
    """Отказ разрешения не должен быть неотличим от отсутствия."""
    from core.best_next_action import unresolved_goal_targets

    assert unresolved_goal_targets(
        "Проследить core/no_such_module и описать", exists=_exists
    ) == ("core/no_such_module",)
    assert unresolved_goal_targets(
        "Проследить core/loop.py и описать", exists=_exists
    ) == ()
    assert unresolved_goal_targets(
        "Разобраться, почему агент повторяет одно и то же", exists=_exists
    ) == ()


def test_a_goal_about_a_missing_file_refuses_by_name() -> None:
    """Красный свидетель: цель тихо теряла силу и агент делал чужую работу.

    Замер 2026-08-26: `resolve_goal_subject` возвращает `None` и когда путь не
    назван, и когда назван несуществующий, — поэтому цель про
    `core/no_such_module` не сужала ничего, и побеждал первый дефект бэклога.
    """
    picked = _pick("Проследить core/no_such_module и описать его места вызова")

    assert picked.action == "observe", picked.action
    assert "core/no_such_module" in picked.reason


def test_a_goal_asking_to_CREATE_a_missing_file_is_not_refused() -> None:
    """Граница: документ, которого ещё нет, — законная цель, а не ошибка.

    Отвергнут вариант «любой несуществующий путь = отказ»: он сломал бы дорогу
    черновиков доктрины, где файл и создаётся.
    """
    picked = _pick(
        "Составь черновик knowledge/doctrine/future/NEW_CONTRACT.md по хартии"
    )

    assert picked.action != "observe" or "NEW_CONTRACT" not in picked.reason


@pytest.mark.parametrize("goal", [
    "Разобраться, почему агент повторяет одно и то же",
    "Проследить core/loop.py и описать его места вызова",
])
def test_goals_that_name_nothing_missing_are_unchanged(goal: str) -> None:
    """Контроль: без несуществующего пути поведение прежнее."""
    assert _pick(goal).action == "improve_failure_to_idea_pipeline"
