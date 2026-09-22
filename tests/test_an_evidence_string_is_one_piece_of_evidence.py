"""Улика-строка — одна улика, а не россыпь букв.

Дефект агента evidence-string-split-into-letters-2026-09-21: `from_dict`
читал `evidence` как `tuple(str(x) for x in ...)`, и строка становилась
кортежем символов (живая запись: 105 элементов, первый — буква 'd').
Правку предложил сам агент в кампании 2026-09-22 (ain_8b6a52ea…); его путь
самоприменения откатил её из-за чужого красного теста, текст — его, дословно.
"""
from __future__ import annotations

from core.self_improvement_issues import SelfImprovementIssue


def test_a_string_is_read_as_one_piece_of_evidence() -> None:
    issue = SelfImprovementIssue.from_dict({"fingerprint": "x", "evidence": "одна строка"})
    assert issue.evidence == ("одна строка",)


def test_a_list_is_still_read_as_a_list() -> None:
    issue = SelfImprovementIssue.from_dict({"fingerprint": "x", "evidence": ["a", "b"]})
    assert issue.evidence == ("a", "b")
