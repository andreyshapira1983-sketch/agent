"""Агент пишет цели двумя словарями, а распознаватель читал один.

Замер, отвергнутые варианты и границы: MIR-174 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from pathlib import Path

from core.best_next_action import resolve_goal_subject
from core.command_subjects import command_module


def _exists(rel: str) -> bool:
    return Path(rel).is_file()


#: Живая цель из ленты кампаний, дословно.
_BY_COMMAND = (
    "Trace `:team-run` sharing of models, sources, logs, and prompts; draft a "
    "reviewed proposal for disclosing correlated evidence in team results."
)


def test_a_command_is_a_subject_too() -> None:
    """Красный свидетель: цель называет команду, и это точный адрес.

    Замер 2026-08-27 по ленте: из шести различных целей три называют путь, ДВЕ
    называют команду `:team-run`, одна — понятие. Команда не расплывчатость: у
    неё есть обработчик в реестре и модуль, где он определён.
    """
    subject = resolve_goal_subject(
        _BY_COMMAND, exists=_exists, command_module=command_module
    )

    assert subject == "cli/commands_team.py", subject


def test_an_unknown_command_is_not_guessed() -> None:
    """Незнакомая команда не даёт предмета: незнание не превращается в адрес."""
    assert resolve_goal_subject(
        "Проследить `:no-such-command` и описать", exists=_exists,
        command_module=command_module,
    ) is None
