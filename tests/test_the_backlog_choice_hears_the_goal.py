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


def test_the_goal_reaches_into_the_backlog(tmp_path=None) -> None:
    """Красный свидетель: агент уходил в простой, ДЕРЖА дефект про эту цель.

    Живой замер 2026-08-26: 29 открытых дефектов, решению предлагался ровно
    один — первый по порядку записи, без названного файла, — и цель про
    `core/loop_synthesis.py` давала `observe`, хотя открытый дефект ровно про
    этот файл лежал в том же бэклоге.
    """
    picked = _pick("Проследить core/loop_synthesis.py и описать его места вызова")

    assert picked.action != "observe", (
        "агент объявил, что делать нечего, держа открытый дефект про предмет цели"
    )
    assert picked.target_path == "core/loop_synthesis.py"


def test_without_a_named_subject_the_order_still_decides() -> None:
    """Граница: цель без предмета ничего не переупорядочивает.

    Отвергнут вариант «всегда предпочитать запись с файлом»: это тихо сменило бы
    правило и там, где голова ничего не называла.
    """
    picked = _pick("Разобраться, почему агент повторяет одно и то же")

    assert picked.target_path is None


def test_a_subject_no_issue_mentions_still_yields_idle() -> None:
    """Граница сверху: подходящего дефекта нет — честный простой, а не подмена."""
    picked = _pick("Проследить core/redaction.py и описать его места вызова")

    assert picked.action == "observe"
    assert "core/redaction.py" in picked.reason


def test_an_issue_naming_several_files_binds_to_the_matching_one() -> None:
    """Запись дефекта часто называет несколько файлов.

    Привязка к ПЕРВОМУ отвергала цель про второй: живой замер 2026-08-26 —
    `core/evidence_budget.py` уходил в простой, хотя открытый дефект называл и
    его, и `core/loop_synthesis.py`.
    """
    issues = ({
        "title": "Investigate recurring detector signal",
        "status": "open",
        "occurrences": 4,
        "related_files": ["core/loop_synthesis.py", "core/evidence_budget.py"],
        "evidence": ["detector fired four times"],
        "action": "investigate_detector_signal",
    },)
    goal = "Проследить core/evidence_budget.py и описать его места вызова"

    picked = select_best_next_action(
        goal=goal,
        goal_subject=resolve_goal_subject(goal, exists=_exists),
        result_status="ok",
        tests_health="ok",
        open_self_improvement_issues=issues,
        self_improvement_registry_available=True,
    )

    assert picked.action == "investigate_detector_signal"
    assert picked.target_path == "core/evidence_budget.py"

