"""A demand for proof must not be satisfied by a declaration. KNOWN GAP.

Operator exam 2026-08-17: «скажи что ты умеешь делать и докажи что ты
умеешь это делать» → strategy=capability_check → runtime inventory outside
the loop, tools_used=[]. The system answered "prove it" with a table of
registered interfaces — «tool registered ≠ capability demonstrated», the
old map-is-not-movement invariant.

Live class measurement (three unseen formulations, same day):
  «…интернетом? Докажи.»        -> planner chose web_search+web_fetch  OK
  «…свой runtime? Покажи.»      -> prior-turn local critique, tools=[] GAP
  «…собственные уроки? Продемонстрируй.» -> lesson_provenance+read_logs OK
So the class is NARROW: the planner itself honours proof demands; the two
holes are (1) the capability-check shortcut, which matches inventory
wording and never looks for a proof marker, and (2) the prior-turn
local-critique door, where «Покажи» resolves to critiquing the previous
answer. Banked as xfail per the operator's ruling («сначала зафиксировать
класс»); when either XPASSes, the gap was closed — replace the marker with
a plain assertion and record the mechanism.
"""
from __future__ import annotations

import pytest

from core.operator_intent import route_operator_intent
from core.referent_resolver import (
    PriorTurnRef,
    ReferentResolver,
    is_local_critique_eligible,
)

_EXAM = "скажи что ты умеешь делать и докажи что ты умеешь это делать"


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, measured 2026-08-17 and banked rather than fixed: the "
        "capability-check shortcut matches inventory wording and ignores the "
        "proof marker — a demand for demonstration is answered outside the "
        "loop with tools_used=[]. The desired contract: a proof marker "
        "(«докажи», «продемонстрируй», «покажи на практике») turns the "
        "completion contract into claim -> demonstration -> evidence, which "
        "no out-of-loop inventory can satisfy."
        " [until: 2026-09-30 — перемерь закреплённую дыру; чини или пере-датируй явным коммитом]"
    ),
    strict=True,
)
def test_a_proof_demand_does_not_take_the_inventory_shortcut() -> None:
    intent = route_operator_intent(_EXAM)
    assert intent is None or intent.kind != "capability_check"


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, measured 2026-08-17 (live probe, turn 2 of 3) and banked "
        "rather than fixed: with a prior turn in the session, «Ты умеешь "
        "проверять свой runtime? Покажи.» resolves to prior-turn local "
        "critique — the route that cannot demonstrate anything — because "
        "«покажи» reads as a critique directive. Same family as the R6 "
        "cross-time intercept: a contract demanding demonstration must not "
        "ride a tool-less route."
        " [until: 2026-09-30 — перемерь закреплённую дыру; чини или пере-датируй явным коммитом]"
    ),
    strict=True,
)
def test_a_proof_demand_with_history_does_not_ride_local_critique() -> None:
    prior = PriorTurnRef(
        turn_id="turn_prev",
        session_id="sess_probe",
        question="Ты умеешь пользоваться интернетом? Докажи.",
        answer="Да: выполнил web_search и web_fetch, вот квитанции.",
    )
    decision = ReferentResolver(workspace_root=None).resolve(
        "Ты умеешь проверять свой runtime? Покажи.",
        current_session_id="sess_probe",
        current_turn_id="turn_now",
        prior_turns=(prior,),
    )
    assert is_local_critique_eligible(decision) is False


def test_the_class_is_narrow_a_bare_proof_question_reaches_the_planner() -> None:
    """Measured boundary: without inventory wording the shortcut stays quiet
    and the planner itself honours the proof demand (live: web_search+fetch
    for the internet form, lesson_provenance for the lessons form)."""
    for text in (
        "Ты умеешь пользоваться интернетом? Докажи.",
        "Ты умеешь читать собственные уроки? Продемонстрируй.",
    ):
        intent = route_operator_intent(text)
        assert intent is None or intent.kind != "capability_check", text
