"""Из бэклога выбирается дефект ПРО ПРЕДМЕТ ЦЕЛИ, а не первый по порядку записи.

Замер, отвергнутые варианты и границы: MIR-160 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from pathlib import Path

from core.best_next_action import resolve_goal_subject, select_best_next_action


def _exists(rel: str) -> bool:
    return Path(rel).is_file()


#: Порядок нарочно такой, как в живом хранилище: первым лежит дефект БЕЗ файла,
#: и именно он до сих пор был единственным, кого видело решение.
_GENERIC_FIRST = {
    "title": "Turn the self-improvement failure into a bounded repair",
    "status": "open",
    "occurrences": 2,
    "evidence": ["generic signal"],
}
_SPECIFIC = {
    "title": "Investigate recurring detector signal: self_contradiction",
    "status": "open",
    "occurrences": 4,
    "related_files": ["core/loop_synthesis.py"],
    "evidence": ["detector fired four times"],
    "action": "investigate_detector_signal",
}
_ISSUES = (_GENERIC_FIRST, _SPECIFIC)


def _pick(goal: str):
    return select_best_next_action(
        goal=goal,
        goal_subject=resolve_goal_subject(goal, exists=_exists),
        result_status="ok",
        tests_health="ok",
        open_self_improvement_issues=_ISSUES,
        self_improvement_registry_available=True,
    )


def test_a_subject_no_issue_mentions_still_yields_idle() -> None:
    """Граница сверху: подходящего дефекта нет — честный простой, а не подмена."""
    picked = _pick("Проследить core/redaction.py и описать его места вызова")

    assert picked.action == "observe"
    assert "core/redaction.py" in picked.reason
