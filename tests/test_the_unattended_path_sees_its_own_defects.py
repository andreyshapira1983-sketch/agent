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


def test_a_resolved_issue_does_not_raise_an_alarm():
    """Закрытый дефект — не повод будить кампанию."""
    resolved = dict(_ISSUE, status="resolved")

    action = select_best_next_action(
        self_improvement_registry_available=True,
        open_self_improvement_issues=(resolved,),
    )

    assert action.action == "observe"
