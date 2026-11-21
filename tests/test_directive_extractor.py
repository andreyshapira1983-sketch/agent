"""Tests for core.directive_extractor.

Two properties matter more than coverage here:

* the four exam scenarios must survive the round trip *from prose*, not just
  from hand-built Directive objects — that is the difference between a gate
  that can fire and one that cannot;
* compatible text must not manufacture a conflict, because a gate that cries
  wolf gets ignored, and then it protects nothing.
"""
from __future__ import annotations

import pytest

from core.directive_extractor import (
    SourceText,
    extract,
    extract_from_task_and_review,
    known_subjects,
)
from core.instruction_conflict_gate import evaluate

# ---------------------------------------------------------------------------
# The registry itself
# ---------------------------------------------------------------------------


def test_known_subjects_are_reported():
    assert "порядок элементов" in known_subjects()
    assert "сетевые вызовы" in known_subjects()


# ---------------------------------------------------------------------------
# Precision: silence when nothing is recognised
# ---------------------------------------------------------------------------

def test_empty_text_yields_nothing():
    assert extract([]) == ()
    assert extract([SourceText("", "advisor", "ревью")]) == ()
    assert extract([SourceText("   \n  ", "advisor", "ревью")]) == ()


def test_agreeing_sources_produce_no_conflict():
    """Same stance, different wording, must not read as a contradiction."""
    directives = extract_from_task_and_review(
        task_text="Порядок вывода должен быть стабильным.",
        review_text="Не меняй порядок элементов, пожалуйста.",
    )
    assert len(directives) == 2
    assert evaluate(directives).mode == "proceed"


@pytest.mark.parametrize("text", [
    "Не сортируй по имени.",
    "Сортировать не надо.",
    "Нельзя сортировать этот список.",
    "Please do not sort by name.",
])
def test_negations_are_recognised(text: str):
    assert extract([SourceText(text, "advisor", "код-ревью")]) == ()


def test_a_refusal_to_sort_does_not_conflict_with_stable_order():
    """The damaging false positive: agreement misread as contradiction."""
    directives = extract_from_task_and_review(
        task_text="Порядок вывода должен быть стабильным.",
        review_text="Не сортируй по имени, пожалуйста.",
    )
    assert evaluate(directives).mode == "proceed"
