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

from core.best_next_action import select_best_next_action


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
