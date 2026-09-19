"""Кампания идёт без человека — значит слепой она быть не вправе.

Background: docs/CODE_NOTES.md, "The unattended path was the blind one".
"""
from __future__ import annotations

import inspect
from pathlib import Path

from core.best_next_action import select_best_next_action
from core.campaign_io import _default_gather_signals, _open_self_improvement_issues

_ISSUE = {
    "id": "sii_test",
    "fingerprint": "sii_f578370c524e6697",
    "status": "open",
    "title": "Turn the self-improvement failure into a bounded repair",
    "action": "improve_failure_to_idea_pipeline",
    "evidence": ["detectors reasoning_action_mismatch, user_contract_unrepresented"],
}


def test_the_selector_acts_on_an_open_issue_when_it_is_given_one():
    """Кандидат в выбирателе есть и работал всегда — не хватало входа."""
    action = select_best_next_action(
        self_improvement_registry_available=True,
        open_self_improvement_issues=(_ISSUE,),
    )

    assert action.action == "improve_failure_to_idea_pipeline"


def test_without_the_issue_list_the_same_state_looks_idle():
    """Тот же мир без этого входа выглядит здоровым.

    Живой прогон 2026-08-15: кампания остановилась с
    `healthy_idle:3_checks_found_nothing_to_do`, ноль вызовов модели — держа
    открытые самонайденные дефекты. Она не ошиблась в рассуждении: ей просто
    не показали список.
    """
    action = select_best_next_action()

    assert action.action == "observe"


def test_the_campaign_now_passes_the_list():
    """Вторая половина дороги: кандидат бесполезен, пока вход даёт только REPL —
    то есть путь, где человек и так смотрит.
    """
    source = inspect.getsource(_default_gather_signals)

    assert "open_self_improvement_issues=open_issues" in source
    assert "self_improvement_registry_available=registry_available" in source


def test_an_unreadable_registry_says_i_do_not_know(tmp_path: Path):
    """Пустой список при нечитаемом хранилище означает «не знаю», и выбиратель
    падает на прежние признаки, а не на выдуманный ноль.
    """
    issues, available = _open_self_improvement_issues(tmp_path)

    assert issues == ()
    assert available is False


def test_a_real_registry_is_read(tmp_path: Path):
    """Улов не отдан: настоящий реестр читается и доходит целиком.

    Запись кладётся ЧЕРЕЗ API реестра, а не строкой в файл: хранилище носит
    контрольную сумму, и подделанная руками строка проверяла бы не то.
    """
    from core.self_improvement_issues import SelfImprovementIssueRegistry

    data = tmp_path / "data"
    data.mkdir()
    registry = SelfImprovementIssueRegistry(data / "self_improvement_issues.jsonl")
    registry.upsert_failure(
        "detectors reasoning_action_mismatch, user_contract_unrepresented",
        "2026-08-15T08:00:00+00:00",
    )

    issues, available = _open_self_improvement_issues(tmp_path)

    assert available is True
    assert len(issues) == 1
    assert issues[0].get("status") == "open"


def test_a_resolved_issue_does_not_raise_an_alarm():
    """Закрытый дефект — не повод будить кампанию."""
    resolved = dict(_ISSUE, status="resolved")

    action = select_best_next_action(
        self_improvement_registry_available=True,
        open_self_improvement_issues=(resolved,),
    )

    assert action.action == "observe"
