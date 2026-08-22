"""A decision must carry how it was selected — the first of the three empty axes.

MIR-117 measured five provenance axes and found three unrecorded: executive
authorship, evidence origin, and reviewer identity. `requested_by` is a
constant component name; the approval surfaces name no actor; and nothing
anywhere says WHO chose the action. So after a run, a defect cannot be
attributed: the record cannot tell the agent's mistake from a developer's.

That gap also blocks the learning question. E (does experience change a later
decision) is only meaningful after D (whose decision is it) — established
earlier in this investigation — and D has no field to be recorded in.

WHAT THIS RECORDS, and it is deliberately a FACT rather than a judgement.
`select_best_next_action` builds candidates that observation activates, then
picks the winner with `max(active, key=priority)` over developer-authored
`_P_*` literals. So exactly one of three things happened, each mechanically
derivable at the selection site:

    no_candidate      nothing was admissible; the observe fallback answered
    sole_candidate    one candidate was active — no selection occurred at all
    priority_table    two or more competed and the developer's numbers picked

The third value is the census's finding made visible at runtime: when the
record says `priority_table`, it is saying a developer decided. There is no
`agent_deliberation` value, because there is no path that would produce one —
inventing the label before the mechanism would be exactly the ceremony this
project keeps finding elsewhere (`expected_effect` is written and read by
nothing).

WHAT THIS DOES NOT DO. It does not record evidence origin or reviewer identity
— the other two empty axes, which live on different surfaces. It does not make
any decision the agent's. And it does not change which action is chosen: the
same input yields the same winner, with one more true thing written down.
"""
from __future__ import annotations

import pytest

from core.best_next_action import select_best_next_action


def test_a_contested_choice_names_the_table_that_settled_it() -> None:
    """Two real problems at once — daemon down (100) and failing tests (80).
    The winner was picked by numbers a developer wrote, and the record must
    say so rather than presenting the outcome as the agent's judgement."""
    action = select_best_next_action(
        heartbeat_missing=True,          # daemon candidate, priority 100
        tests_health="fail",             # tests candidate, priority 80
        failed_tests=("tests/test_x.py",),
    )
    assert action.decided_by == "priority_table", (
        f"a contested selection recorded {action.decided_by!r} — the developer's "
        "numbers picked the winner and the record does not say so"
    )
    assert action.candidates_considered >= 2


def test_a_single_admissible_action_is_not_a_choice() -> None:
    """One candidate means nothing was selected. Calling that a decision would
    inflate the record exactly where it must not."""
    action = select_best_next_action(tests_health="fail",
                                     failed_tests=("tests/test_x.py",))
    assert action.decided_by == "sole_candidate"
    assert action.candidates_considered == 1


def test_the_observe_fallback_admits_that_nothing_was_chosen() -> None:
    """A clean world produces the observe action. Nothing competed, and the
    record must not imply a judgement was made."""
    action = select_best_next_action(tests_health="pass", result_status="done")
    assert action.decided_by == "no_candidate"
    assert action.candidates_considered == 0


def test_no_path_claims_the_agent_deliberated() -> None:
    """The census measured zero agent-owned decision boundaries. Until one
    exists, no run may produce a record saying the agent decided — a label
    without a mechanism is the ceremony this project keeps finding."""
    for kwargs in (
        {},
        {"tests_health": "fail", "failed_tests": ("t.py",)},
        {"heartbeat_missing": True, "tests_health": "fail",
         "failed_tests": ("t.py",)},
        {"tick_error": "boom"},
        {"inbox_pending": 5},
    ):
        action = select_best_next_action(**kwargs)
        assert action.decided_by in {
            "no_candidate", "sole_candidate", "priority_table",
        }, f"unexpected provenance {action.decided_by!r} for {kwargs}"


def test_the_provenance_reaches_the_record_not_only_the_object() -> None:
    """A distinction that never leaves the process cannot be audited after the
    run — the whole reason the three axes were called a gap."""
    action = select_best_next_action(
        heartbeat_missing=True, tests_health="fail", failed_tests=("t.py",)
    )
    payload = action.to_dict()
    assert payload["decided_by"] == "priority_table"
    assert payload["candidates_considered"] >= 2


def test_the_chosen_action_itself_is_unchanged() -> None:
    """Recording provenance must not move a single verdict. Same world, same
    winner — one more true thing written beside it."""
    action = select_best_next_action(
        heartbeat_missing=True, tests_health="fail", failed_tests=("t.py",)
    )
    assert action.action == "restart_daemon" or action.priority == 100, (
        f"the winner changed: {action.action!r} at priority {action.priority}"
    )


@pytest.mark.parametrize("acknowledged,expected_min", [
    (frozenset(), 2),
    (frozenset({"review_inbox_backlog"}), 1),
])
def test_the_count_reflects_what_actually_competed(
    acknowledged: frozenset, expected_min: int
) -> None:
    """Suppressed candidates leave the race, so they were not competitors. The
    count must describe the race that happened, not the one that might have."""
    action = select_best_next_action(
        heartbeat_missing=True, tests_health="fail", failed_tests=("t.py",),
        acknowledged=acknowledged,
    )
    assert action.candidates_considered >= expected_min
