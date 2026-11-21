"""A decision must record where its grounds came from — the last empty axis.

MIR-117 named five provenance axes. Two are now recorded: how the action was
selected (`decided_by` on the action) and who gave a verdict (`decided_by` on
the approval). This is the third and last: WHERE THE GROUNDS CAME FROM.

Why it is not cosmetic. It is the axis that makes MIR-117's third
false-positive path detectable — human-deferred authorship, where a human's
choice at t1 is stored, changes an outcome at t2, and reads as the agent
learning. And it is the axis the operator's own ratified rule turns on:
supplying a fact leaves the executive choice with the agent; naming the next
move does not. Without this field the two are indistinguishable after the run.

Three origins, each derivable from WHICH INPUT a candidate read — a fact, not a
judgement:

    operator_goal    the grounds are the operator's own text (the campaign goal)
    observed_state   live signals: heartbeat, test results, inbox counts, streaks
    retained_record  durable state carried from earlier runs — the self-improvement
                     issue registry and recent failures

Measured before writing: three of the twelve candidate generators take `goal`
and nine take signals, so the split is mechanical rather than assigned.

The third value matters most for what comes next. "Did experience change this
decision" is only answerable if the record says the grounds were retained from
before — which is exactly the D-before-E ordering established earlier in this
investigation.

NOT claimed: that an `operator_goal` decision is the human deciding. The
operator naming a subject and the operator naming the next move are different
things, and this field records only where the grounds came from, not who
exercised judgement over them.
"""
from __future__ import annotations

from core.best_next_action import select_best_next_action


def test_a_goal_driven_action_says_its_grounds_are_the_operators_text() -> None:
    """The goal asks to study the outside world; the grounds for that action are
    the operator's sentence, not anything the agent observed."""
    action = select_best_next_action(
        goal="изучи в интернете https://example.org про агентов",
        tests_health="pass", result_status="done",
    )
    assert action.grounds == "operator_goal", (
        f"an action derived from the goal text recorded {action.grounds!r}"
    )


def test_a_decision_resting_on_stored_history_says_so() -> None:
    """The path that makes the learning question answerable: these grounds were
    carried from earlier runs, so a later claim that experience changed the
    decision has something to rest on."""
    action = select_best_next_action(
        tests_health="pass", result_status="done",
        recent_self_improvement_failures=(
            "self-apply rolled_back: duplicate base class",
        ),
    )
    assert action.grounds == "retained_record", (
        f"an action driven by stored history recorded {action.grounds!r} — the "
        "memory-influence path stays invisible"
    )
