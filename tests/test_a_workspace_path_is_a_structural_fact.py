"""Naming a file in the repo is a fact about the turn, not a word to match.

Background: docs/CODE_NOTES.md, "A path is a fact, a word is a guess".
"""
from __future__ import annotations

import pytest

from core.doc_routing import _is_self_repo_introspection_question
from core.operator_intent import route_operator_intent

# Phrasings that name something inside the workspace. None of them appears in
# any of the tables they must defeat — the operator's rule is that a repair
# counts only when an UNSEEN form of the class is stopped.
_NAMES_A_PATH = [
    "Открой core/loop.py и посмотри на планировщика.",
    "В core/loop_synthesis.py посмотри, что делает планировщик с бюджетом.",
    "Загляни в tools/read_logs.py — там что-то не так с планом чтения.",
    "Сверь tests/conftest.py и core/planner.py, менять ли что-то.",
    "Посмотри docs/OPERATIONS.md, какие тесты там упомянуты.",
]

# Genuine plan orders that name nothing inside the repo. These must keep routing
# to the plan command: the fix may not buy correctness by refusing to route.
_ASKS_FOR_A_PLAN = [
    "Составь точный план реализации операторского слоя задач.",
    "Дай implementation plan для новой подсистемы уведомлений.",
]


@pytest.mark.parametrize("text", _NAMES_A_PATH)
def test_naming_a_repo_file_is_not_an_order_for_a_plan(text: str):
    """Measured live 2026-08-15: «посмотри на планировщика» + a path routed to
    `:implementation-plan`, 8 events, no model call — the substring «план»
    lives inside «планировщик», the name of the agent's own node.
    """
    assert route_operator_intent(text) is None, (
        f"a task naming a workspace file was taken as an order for a plan: {text!r}"
    )


@pytest.mark.parametrize("text", _ASKS_FOR_A_PLAN)
def test_a_real_plan_request_still_routes(text: str):
    """The guard against overreach: without a repo path this IS a plan order."""
    intent = route_operator_intent(text)
    assert intent is not None and "plan" in intent.kind


@pytest.mark.parametrize("text", _NAMES_A_PATH)
def test_naming_a_repo_file_makes_it_introspection(text: str):
    """The public web cannot answer a question about a file in this repo.

    `_SELF_REPO_INTROSPECTION_TERMS` holds 51 phrases and none of these match,
    so the planner searched the web for `SynthesisState tests site:tests/`
    three identical times and exhausted its replan budget.
    """
    assert _is_self_repo_introspection_question(text), (
        f"a question about a file in this repository was allowed to reach the "
        f"public web: {text!r}"
    )


def test_an_outward_question_is_still_outward():
    """A path-shaped token is not enough when the question wants the world."""
    assert not _is_self_repo_introspection_question(
        "Найди в интернете свежие новости про numpy.py и что о нём пишут."
    )
