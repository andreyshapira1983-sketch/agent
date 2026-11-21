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

_OTHER_SUBJECT = {
    "title": "redaction misses a shape",
    "status": "open",
    "occurrences": 2,
    "related_files": ["core/redaction.py"],
    "evidence": ["detector fired twice"],
}


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
