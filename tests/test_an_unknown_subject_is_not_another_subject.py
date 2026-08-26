"""«Про другой предмет» и «предмет неизвестен» — разные основания отвода.

Замер, отвергнутые варианты и границы: MIR-162 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from pathlib import Path

from core.best_next_action import (
    resolve_goal_subject,
    select_best_next_action,
    unresolved_goal_targets,
)


def _exists(rel: str) -> bool:
    return Path(rel).is_file()


_GOAL = "Проследить core/loop.py и описать его места вызова"

_NO_SUBJECT = {
    "title": "something repeats and nobody knows why",
    "status": "open",
    "occurrences": 2,
    "evidence": ["detector fired twice"],
}
_OTHER_SUBJECT = {
    "title": "redaction misses a shape",
    "status": "open",
    "occurrences": 2,
    "related_files": ["core/redaction.py"],
    "evidence": ["detector fired twice"],
}


def _pick(issues):
    return select_best_next_action(
        goal=_GOAL,
        goal_subject=resolve_goal_subject(_GOAL, exists=_exists),
        goal_names_missing=unresolved_goal_targets(_GOAL, exists=_exists),
        result_status="ok",
        tests_health="ok",
        open_self_improvement_issues=issues,
        self_improvement_registry_available=True,
    )


def test_an_unclassifiable_candidate_is_not_called_off_subject() -> None:
    """Красный свидетель: у записи БЕЗ файла предмет неизвестен, а не «другой».

    Замер 2026-08-26: обе ситуации давали дословно одну причину — «кандидаты
    про другой предмет». На живых данных это 21 запись из 29: их молча
    записывали в «про другое», хотя про что они — не знает никто.
    """
    picked = _pick((_NO_SUBJECT,))

    assert picked.action == "observe"
    assert "another subject" not in picked.reason, (
        "запись без названного файла объявлена «про другой предмет» — "
        "решение выдаёт незнание за знание"
    )
    assert "subject" in picked.reason.lower()


def test_a_genuinely_other_subject_keeps_its_wording() -> None:
    """Контроль: там, где предмет ИЗВЕСТЕН и другой, формулировка прежняя."""
    picked = _pick((_OTHER_SUBJECT,))

    assert picked.action == "observe"
    assert "another subject" in picked.reason


def test_the_two_grounds_are_counted_apart() -> None:
    """Оператору нужны ЧИСЛА, чтобы решить политику, а не общее слово.

    Из бэклога в гонку выходит ровно ОДИН кандидат (первый незакрытый), поэтому
    два класса сразу дают разные источники: дефект про другой файл и застой
    сухих прогонов, у которого предмета нет вовсе.
    """
    picked = select_best_next_action(
        goal=_GOAL,
        goal_subject=resolve_goal_subject(_GOAL, exists=_exists),
        goal_names_missing=unresolved_goal_targets(_GOAL, exists=_exists),
        result_status="ok",
        tests_health="ok",
        dry_run_streak=6,
        open_self_improvement_issues=(_OTHER_SUBJECT,),
        self_improvement_registry_available=True,
    )
    joined = " | ".join(picked.evidence)

    assert picked.action == "observe"
    assert "unknown subject" in joined, joined
    assert "off-subject" in joined, joined
    assert "1 about another subject" in picked.reason
    assert "naming no subject at all" in picked.reason


def test_both_kinds_still_stay_out_of_the_race() -> None:
    """Поведение НЕ меняется: отвод остаётся отводом, меняется его честность."""
    assert _pick((_NO_SUBJECT,)).action == "observe"
    assert _pick((_OTHER_SUBJECT,)).action == "observe"
    assert _pick((_NO_SUBJECT, _OTHER_SUBJECT)).action == "observe"
