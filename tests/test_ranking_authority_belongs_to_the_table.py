"""WHO DECIDES, first measurement: who ranks two admissible actions.

Narrow on purpose. This says nothing about whether the agent is autonomous in
general, and nothing about observation being worthless — observation decides
which candidates EXIST, and the third test below pins that. The question here
is only what happens once two of them are admissible at the same time.

`core/best_next_action.py` gives every candidate a constant written by a
developer — `_P_TICK_ERROR = 90`, `_P_TESTS_FAIL = 80`, and a dozen more — and
then selects with `max(active, key=lambda c: c.priority)` (:319).

The experiment holds the observable facts fixed and changes only the relation
between two of those numbers:

    facts: a tick errored AND tests are failing — both true, both admissible
    as written        -> investigate_tick_error        (90 beats 80)
    weights flipped   -> propose_minimal_test_repair   (95 beats 90)

Same world, different winner. So at this boundary the agent does not determine
the relative value of two available actions; the numbers do.

What that licenses, exactly: ranking authority at THIS site belongs to a table
in Python. Not "the whole autonomy is theatre", not "signals are ignored" —
those would be the same overclaim this audit keeps catching.
"""
from __future__ import annotations

import pytest

import core.best_next_action as bna

#: A world in which two candidates are admissible at once. Kept as one dict so
#: every test below sees the identical facts — that is the whole design.
_BOTH_ADMISSIBLE = {
    "result_status": "failed",
    "tests_health": "failing",
    "failed_tests": ("tests/test_a.py::test_x",),
    "tick_error": "ValueError: boom",
}


def test_with_both_admissible_the_higher_constant_wins() -> None:
    """Today's behaviour, recorded before anything is changed."""
    chosen = bna.select_best_next_action(**_BOTH_ADMISSIBLE)
    assert chosen.action == "investigate_tick_error"
    assert chosen.priority == bna._P_TICK_ERROR
    assert bna._P_TICK_ERROR > bna._P_TESTS_FAIL, (
        "the two constants no longer stand in the relation this measurement "
        "was built on — re-measure before trusting the case below"
    )


def test_the_winner_follows_the_constants_not_the_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The causal half. Nothing about the world changes between these two
    calls; only the relation between two developer-written numbers does."""
    before = bna.select_best_next_action(**_BOTH_ADMISSIBLE)

    monkeypatch.setattr(bna, "_P_TESTS_FAIL", bna._P_TICK_ERROR + 5)
    after = bna.select_best_next_action(**_BOTH_ADMISSIBLE)

    assert before.action != after.action, (
        "the winner did not move with the weights, so ranking is decided by "
        "something else and this measurement is pointed at the wrong site"
    )
    assert after.action == "propose_minimal_test_repair"


def test_observation_still_decides_which_candidates_exist() -> None:
    """The boundary that keeps the claim honest.

    Without this the file would read as "the agent ignores what it sees",
    which is false and is the kind of overclaim this audit exists to catch. The
    facts DO decide admission: remove the tick error and the other candidate
    wins on the same table, unchanged."""
    facts = dict(_BOTH_ADMISSIBLE)
    facts["tick_error"] = None
    chosen = bna.select_best_next_action(**facts)
    assert chosen.action == "propose_minimal_test_repair", (
        "with the tick error gone the failing tests should carry the choice"
    )

    quiet = bna.select_best_next_action(result_status="ok", tests_health="passing")
    assert quiet.action != "investigate_tick_error"
