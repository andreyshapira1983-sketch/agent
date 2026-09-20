"""Дефект, мешающий работе, не выметается чужой целью.

Слово оператора 2026-09-20: «почини фильтр, пусть его собственные дефекты
проходят».

Замер того вечера. За прогон принято 117 решений, и основание у ВСЕХ 117 —
`operator_goal`; по собственному реестру дефектов не принято ни одного.
Причина в двух местах, и по отдельности каждое выглядит разумным:

  * `_partition_by_subject` оставляет кандидата в гонке, только если его
    тяжесть критическая или высокая, либо его предмет совпадает с предметом
    цели, либо он пришёл из цели. Правило написано не зря: без него цель не
    влияла ни на что (MIR-162);
  * `_candidate_open_self_improvement_issue` ставил кандидату тяжесть
    `medium` КОНСТАНТОЙ.

Вместе они значат вот что: всё, что агент узнаёт о СЕБЕ, выметается с доски
любой целью, называющей другой файл, — каким бы тяжёлым ни было. Дверь
«объективная помеха не выметается» существовала и работала; её заварили
изнутри одной строкой.

Починка отпирает дверь, не ломая стену: запись вправе назвать свою тяжесть,
и словарь её закрыт на `high`. `critical` остаётся за объективной
неживостью — демон упал, тик сломан, — то есть за тем, что видно снаружи и
не зависит от самооценки. Запись без тяжести читается как `medium`, ровно
как раньше, поэтому цель не теряет влияния над досадой.
"""
from __future__ import annotations

from core.best_next_action import _candidate_open_self_improvement_issue
from core.self_improvement_issues import SelfImprovementIssue

_GOAL_SUBJECT = "core/step_sanitizer.py"


def _issue(**over) -> dict:
    base = {
        "fingerprint": "propose-engineering-task-never-ran-2026-09-20",
        "title": "propose_engineering_task disabled before start",
        "action": "improve_failure_to_idea_pipeline",
        "status": "open",
        "evidence": ["core/campaign.py:808-811 — ran=false counted as empty"],
        "related_files": ["core/campaign.py"],
        "suggested_next_action": "count the cycles where ran is false",
    }
    base.update(over)
    return base


def _severity_of(issue: dict) -> str:
    candidate = _candidate_open_self_improvement_issue((issue,))
    assert candidate is not None, "запись не стала кандидатом вовсе"
    return candidate.severity


def test_a_defect_that_blocks_the_work_may_say_so() -> None:
    """Высокая тяжесть доезжает до гонки и переживает чужую цель."""
    assert _severity_of(_issue(severity="high")) == "high"


def test_a_nuisance_still_yields_to_the_goal() -> None:
    """Без заявленной тяжести — прежнее поведение, цель сохраняет влияние."""
    assert _severity_of(_issue()) == "medium"


def test_a_record_cannot_declare_itself_an_outage() -> None:
    """`critical` принадлежит объективной неживости, не самооценке."""
    assert _severity_of(_issue(severity="critical")) == "medium"


def test_an_unknown_word_is_read_as_the_old_behaviour() -> None:
    assert _severity_of(_issue(severity="катастрофа")) == "medium"


def test_the_stored_record_carries_the_word_back() -> None:
    """Тяжесть переживает запись на диск и чтение обратно."""
    restored = SelfImprovementIssue.from_dict(_issue(severity="high"))
    assert restored.severity == "high"
    assert restored.to_dict()["severity"] == "high"
    plain = SelfImprovementIssue.from_dict(_issue())
    assert plain.severity == "medium"
