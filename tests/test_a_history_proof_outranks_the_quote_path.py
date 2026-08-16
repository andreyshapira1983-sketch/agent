"""A question demanding a cross-time proof must not ride the tool-less quote path.

Live failure (operator exam, 2026-08-16, run_59b740111): the causal-use
question arrived wrapped in guillemets, the resolver saw an explicit quote,
local critique fired with tools=[], and nano invented four citations.  The
route that by construction cannot fetch historical evidence was chosen for a
question whose whole subject IS a historical relation (lesson -> later action).
Contract under test: when the directive demands proving a relation between
the agent's own past and a later action, ``is_local_critique_eligible`` yields
so the planner runs an evidence-producing plan.
"""
from __future__ import annotations

from core.referent_resolver import (
    PriorTurnRef,
    ReferentResolver,
    is_local_critique_eligible,
)

SESSION = "sess_test"
TURN = "turn_current"

# Verbatim from data/episodic_memory.jsonl, run_59b740111.
EXAM_QUESTION = (
    "«Назови один урок, который ты получил из собственного прошлого опыта, "
    "и покажи конкретный более поздний случай, где этот урок реально изменил "
    "твой план или действие. Если такого случая нет — скажи, что causal use "
    "не доказан.»"
)


def _resolve(text: str, prior_turns=()):  # noqa: ANN001 - test helper
    return ReferentResolver(workspace_root=None).resolve(
        text,
        current_session_id=SESSION,
        current_turn_id=TURN,
        prior_turns=prior_turns,
    )


def test_the_exam_question_does_not_ride_local_critique() -> None:
    decision = _resolve(EXAM_QUESTION)
    assert is_local_critique_eligible(decision) is False


def test_an_unseen_form_of_the_class_is_also_stopped() -> None:
    """Same class, no exam wording: repair -> later behaviour change."""
    question = (
        "«Докажи, что после ремонта верификатора твоё поведение в более "
        "позднем прогоне действительно изменилось: покажи эпизод до и "
        "эпизод после.»"
    )
    decision = _resolve(question)
    assert is_local_critique_eligible(decision) is False


def test_a_brought_quote_critique_still_rides() -> None:
    """Control: critiquing a brought text stays on the local path."""
    question = (
        'Покажи слабые стороны этого текста: "Рынок вырастет в десять раз, '
        'потому что все так говорят, и никакие проверки не нужны."'
    )
    decision = _resolve(question)
    assert decision.status == "resolved"
    assert is_local_critique_eligible(decision) is True


def test_a_prior_turn_critique_still_rides() -> None:
    """Control: prior-turn critique (the classic PR2 path) is untouched."""
    prior = PriorTurnRef(
        turn_id="turn_prev",
        session_id=SESSION,
        question="план",
        answer="Категоричный текст без доказательств о росте рынка.",
    )
    decision = _resolve("покажи слабые стороны этого", prior_turns=(prior,))
    assert decision.status == "resolved"
    assert is_local_critique_eligible(decision) is True
