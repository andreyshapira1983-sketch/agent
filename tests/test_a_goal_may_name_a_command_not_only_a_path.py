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
_BY_PATH = "Trace `core/subagent_registry` and its call sites"
_BY_CONCEPT = "Trace current claim-status mutation paths and draft a proposal"


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


def test_a_path_still_wins_and_still_works() -> None:
    """Контроль: прежний словарь не пострадал."""
    subject = resolve_goal_subject(
        _BY_PATH, exists=_exists, command_module=command_module
    )

    assert subject == "core/subagent_registry.py"


def test_a_concept_still_names_nothing() -> None:
    """Понятие остаётся нераспознанным — и это честно, а не недоработка.

    Что делать с такой целью — вопрос политики, вынесенный оператору. Пока
    молча угадывать за неё предмет было бы хуже, чем признать незнание.
    """
    assert resolve_goal_subject(
        _BY_CONCEPT, exists=_exists, command_module=command_module
    ) is None


def test_an_unknown_command_is_not_guessed() -> None:
    """Незнакомая команда не даёт предмета: незнание не превращается в адрес."""
    assert resolve_goal_subject(
        "Проследить `:no-such-command` и описать", exists=_exists,
        command_module=command_module,
    ) is None


def test_the_old_signature_still_works() -> None:
    """Без словаря команд поведение прежнее — вызывающие не ломаются."""
    assert resolve_goal_subject(_BY_PATH, exists=_exists) == "core/subagent_registry.py"
    assert resolve_goal_subject(_BY_COMMAND, exists=_exists) is None


def test_the_map_is_built_from_the_registry_not_from_a_list() -> None:
    """Карта выводится, а не ведётся руками: рукописная разойдётся с кодом.

    Команда → `handler_key` из реестра → модуль, где определён `_handle_<key>`.
    Обе половины уже существуют; здесь они только соединены.
    """
    assert command_module(":team-run") == "cli/commands_team.py"
    assert command_module(":campaign-start") == "app/runtime_cli.py"
    assert command_module(":no-such-command") is None
