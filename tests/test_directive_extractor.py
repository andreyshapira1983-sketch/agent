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
    AXES,
    SourceText,
    extract,
    extract_from_task_and_review,
    known_subjects,
)
from core.instruction_conflict_gate import evaluate


def _subjects(directives) -> set[str]:
    return {d.subject for d in directives}


def _demand_for(directives, subject: str, level: str) -> str | None:
    for d in directives:
        if d.subject == subject and d.source_level == level:
            return d.demand
    return None


# ---------------------------------------------------------------------------
# The registry itself
# ---------------------------------------------------------------------------

def test_every_axis_has_at_least_two_exclusive_stances():
    """An axis with one stance can never detect a conflict."""
    for axis in AXES:
        assert len(axis.stances) >= 2, axis.subject


def test_stance_demands_are_unique_within_an_axis():
    """Two stances sharing a demand label would silently never conflict."""
    for axis in AXES:
        labels = [stance.demand for stance in axis.stances]
        assert len(labels) == len(set(labels)), axis.subject


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


def test_unrelated_text_yields_nothing():
    directives = extract([
        SourceText(
            "Добавь докстринг к функции и поправь опечатку в комментарии.",
            "advisor",
            "код-ревью",
        ),
    ])
    assert directives == (), (
        "prose on no known axis must produce no directive — a false conflict "
        "blocks real work"
    )


def test_agreeing_sources_produce_no_conflict():
    """Same stance, different wording, must not read as a contradiction."""
    directives = extract_from_task_and_review(
        task_text="Порядок вывода должен быть стабильным.",
        review_text="Не меняй порядок элементов, пожалуйста.",
    )
    assert len(directives) == 2
    assert evaluate(directives).mode == "proceed"


def test_repeated_stance_from_one_source_is_one_directive():
    directives = extract([
        SourceText(
            "Отсортируй по имени. Правда, лучше отсортировать. "
            "И вообще сортировка тут нужна.",
            "advisor",
            "код-ревью",
        ),
    ])
    assert len(directives) == 1


# ---------------------------------------------------------------------------
# The four exam scenarios, extracted from prose
# ---------------------------------------------------------------------------

def test_scenario_stable_order_vs_sort_by_name():
    directives = extract_from_task_and_review(
        task_text="Порядок вывода должен быть стабильным между запусками.",
        review_text="Отсортируй по имени, так читается удобнее.",
    )
    outcome = evaluate(directives)
    assert outcome.is_blocked
    assert _demand_for(directives, "порядок элементов", "task_contract") == (
        "сохранять стабильный порядок"
    )
    assert _demand_for(directives, "порядок элементов", "advisor") == "сортировать"


def test_scenario_no_network_vs_fetch_via_api():
    directives = extract_from_task_and_review(
        task_text="Сетевые вызовы запрещены, работаем офлайн.",
        review_text="Эти данные проще получить через API.",
    )
    outcome = evaluate(directives)
    assert outcome.is_blocked
    assert "сетевые вызовы" in _subjects(directives)


def test_scenario_exception_propagate_vs_swallow():
    directives = extract([
        SourceText(
            "Тест требует, чтобы исключение пробрасывалось наружу.",
            "test_expectation",
            "tests/test_loader.py",
        ),
        SourceText(
            "Давай для удобства проглотим это исключение.",
            "advisor",
            "код-ревью",
        ),
    ])
    outcome = evaluate(directives)
    assert outcome.is_blocked
    assert outcome.findings[0].higher.source_level == "test_expectation"


def test_scenario_no_edits_vs_module_registration():
    directives = extract([
        SourceText(
            "Существующие файлы не менять, добавляем только новый модуль.",
            "task_contract",
            "постановка задачи",
        ),
        SourceText(
            "Новый модуль надо зарегистрировать в индексе.",
            "repo_invariant",
            "docs/AGENT_DOCTRINE.md",
        ),
    ])
    outcome = evaluate(directives)
    assert outcome.is_blocked
    assert "изменение существующих файлов" in _subjects(directives)


def test_scenario_reviewer_asks_to_weaken_a_test():
    directives = extract([
        SourceText(
            "Тесты не изменять.", "task_contract", "постановка задачи",
        ),
        SourceText(
            "Проще ослабить проверку в тесте.", "advisor", "код-ревью",
        ),
    ])
    assert evaluate(directives).is_blocked


# ---------------------------------------------------------------------------
# Authority comes from the channel, never from the wording
# ---------------------------------------------------------------------------

def test_authority_is_taken_from_the_source_not_the_tone():
    """The most commanding sentence in the batch is still only an advisor."""
    directives = extract([
        SourceText(
            "КРИТИЧНО: немедленно отсортируй по имени, это обязательно!",
            "advisor",
            "код-ревью",
        ),
        SourceText(
            "порядок стабильный", "task_contract", "постановка задачи",
        ),
    ])
    outcome = evaluate(directives)
    assert outcome.is_blocked
    assert outcome.findings[0].higher.source_level == "task_contract", (
        "an emphatic review comment must not outrank a quietly worded contract"
    )


@pytest.mark.parametrize("level", [
    "operator", "task_contract", "test_expectation", "repo_invariant",
    "local_convention", "advisor",
])
def test_every_level_survives_extraction(level: str):
    directives = extract([
        SourceText("Отсортируй по имени.", level, "источник"),  # type: ignore[arg-type]
    ])
    assert len(directives) == 1
    assert directives[0].source_level == level


# ---------------------------------------------------------------------------
# Quoting and sentence handling
# ---------------------------------------------------------------------------

def test_quote_is_the_verbatim_sentence():
    directives = extract([
        SourceText(
            "Сначала общие слова. Отсортируй по имени, так удобнее. И ещё текст.",
            "advisor",
            "код-ревью",
        ),
    ])
    assert directives[0].quote == "Отсортируй по имени, так удобнее."


def test_bullet_lines_are_separate_sentences():
    directives = extract([
        SourceText(
            "- порядок должен быть стабильным\n- сетевые вызовы запрещены",
            "task_contract",
            "постановка задачи",
        ),
    ])
    assert _subjects(directives) == {"порядок элементов", "сетевые вызовы"}


def test_locator_is_carried_into_the_directive():
    directives = extract([
        SourceText(
            "Отсортируй по имени.", "advisor", "код-ревью", locator="PR#214#c9",
        ),
    ])
    assert directives[0].locator == "PR#214#c9"
    assert "PR#214#c9" in directives[0].cite()


def test_english_wording_is_recognised():
    directives = extract_from_task_and_review(
        task_text="The output order must be stable across runs.",
        review_text="Please sort by name instead.",
    )
    assert evaluate(directives).is_blocked


def test_extraction_is_deterministic():
    def build():
        return extract_from_task_and_review(
            task_text="Порядок стабильный. Сетевые вызовы запрещены.",
            review_text="Отсортируй по имени. Возьми через API.",
        )

    assert [d.to_dict() for d in build()] == [d.to_dict() for d in build()]
