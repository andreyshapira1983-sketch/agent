"""The backfill may only replay a writer's own rule — never guess.

`scripts/completion_backfill.py` exists because two writers banked episodes before
they learned to settle the completion axis. Replaying their rule over those rows
recovers a verdict; applying the same table to anything else would re-open the
inference MIR-057 forbids, since a cycle-banked row's `outcome` says nothing
about whether its goal was reached.

These tests pin that boundary: the signature must be proved first, and every row
the module does not positively recognise must come back unclassified.
"""

from __future__ import annotations

import pytest

from scripts.completion_backfill import writer_backfill_verdict


def _self_build_row(outcome: str, **over) -> dict:
    row = {
        "goal": "produce self-build patch for cli/intent_bridge.py",
        "question": "self-task-produce",
        "outcome": outcome,
        "tags": ["self-build", "lesson", "self-task-produce", "proposed", outcome],
    }
    row.update(over)
    return row


def _self_repair_row(**over) -> dict:
    row = {
        "goal": "repair",
        "question": "fix core/loop_methods2.py",
        "outcome": "success",
        "tags": ["lesson", "bug-fix", "regression-guard"],
    }
    row.update(over)
    return row


@pytest.mark.parametrize(
    "outcome,expected",
    [("success", "achieved"), ("partial", "partially_achieved"), ("failed", "failed")],
)
def test_self_build_row_gets_the_verdict_its_writer_would_settle(outcome, expected):
    assert writer_backfill_verdict(_self_build_row(outcome)) == expected


def test_self_repair_row_is_achieved_because_a_lesson_means_it_verified_clean():
    assert writer_backfill_verdict(_self_repair_row()) == "achieved"


def test_a_cycle_banked_row_is_never_classified_from_its_outcome():
    """The guard the whole module exists for.

    This row carries `outcome="success"` and no verdict — exactly the shape the
    table would happily map. It must not be mapped: nothing here was banked by a
    mechanical writer, so `outcome` answers "were the claims supported", not
    "was the goal reached", and inferring one from the other is the MIR-057
    error. A missing signature has to mean "leave it alone".
    """
    cycle_row = {
        "goal": "explain the difference between REST and GraphQL",
        "question": "explain the difference between REST and GraphQL",
        "outcome": "success",
        "tags": ["lesson"],
    }
    assert writer_backfill_verdict(cycle_row) is None


def test_a_row_that_already_carries_a_verdict_is_never_re_decided():
    """MIR-057: the axis is settled once, at banking, and never recomputed."""
    settled = _self_build_row("failed", completion_state="blocked")
    assert writer_backfill_verdict(settled) is None


@pytest.mark.parametrize("outcome", ["", None, "rolled_back", "proposed", "unknown"])
def test_a_recognised_writer_with_unusable_evidence_still_yields_nothing(outcome):
    """A signature is a licence to replay the rule, not to invent a verdict."""
    row = _self_build_row("success")
    row["outcome"] = outcome
    assert writer_backfill_verdict(row) is None


@pytest.mark.parametrize(
    "over",
    [
        {"goal": "not-repair"},
        {"tags": ["lesson", "bug-fix"]},
        {"tags": ["lesson", "regression-guard"]},
        {"tags": []},
    ],
)
def test_a_partial_repair_signature_does_not_count_as_a_signature(over):
    assert writer_backfill_verdict(_self_repair_row(**over)) is None


def test_backfill_and_the_live_writer_read_the_same_table():
    """One rule, not two copies that drift.

    If a future change gives either side its own table, this fails — which is
    the only mechanical way to keep "the backfill replays the writer's rule"
    true rather than merely intended.
    """
    from core.self_build_memory import _COMPLETION_BY_OUTCOME
    from core.writer_completion import COMPLETION_BY_OUTCOME

    assert _COMPLETION_BY_OUTCOME is COMPLETION_BY_OUTCOME


# ==========================================================================
# The tag is a category, not a provenance proof.
# ==========================================================================
def test_a_cycle_banked_row_wearing_the_self_build_tag_is_not_classified():
    """The tag says what a record is ABOUT, not who banked it.

    A cycle-banked answer to a question about self-build can carry the tag —
    the user's own topic tags reach the episode. Its `outcome` was decided by
    claim verification, so mapping it would be exactly the inference MIR-057
    forbids. The composite signature is what keeps it out.
    """
    row = {
        "goal": "explain how self-build picks a target",
        "question": "explain how self-build picks a target",
        "outcome": "success",
        "tags": ["self-build", "lesson"],
    }
    assert writer_backfill_verdict(row) is None


@pytest.mark.parametrize(
    "question",
    ["self-build", "self-build-review", "fix core/loop.py", "", None, "produce"],
)
def test_a_question_outside_the_writers_call_sites_is_not_classified(question):
    """The whitelist comes from `build_self_build_episode` callers, and a
    trigger that is not one of them may settle completion differently."""
    row = _self_build_row("success")
    row["question"] = question
    row["tags"] = ["self-build", "lesson", str(question), "success"]
    assert writer_backfill_verdict(row) is None


def test_a_self_build_question_without_the_writers_tags_is_not_classified():
    """The writer emits `["self-build", "lesson", kind, ...]` every time; a row
    holding the question but not those tags was assembled by something else."""
    row = _self_build_row("success", tags=["lesson", "success"])
    assert writer_backfill_verdict(row) is None


@pytest.mark.parametrize(
    "tags",
    ["self-build", 42, None, {"self-build": True}, ["self-build", 7], [None]],
)
def test_malformed_tags_fail_closed(tags):
    """A migration that cannot read a row's schema must not decide its verdict.

    `set("self-build")` is a set of characters, so a naive membership test on a
    bare string is meaningless rather than merely wrong — and it would pass.
    """
    row = _self_build_row("success", tags=tags)
    assert writer_backfill_verdict(row) is None


# ==========================================================================
# A repair verdict must be backed by the outcome it claims.
# ==========================================================================
@pytest.mark.parametrize("outcome", ["failed", "partial", "", None, "repaired"])
def test_a_repair_signature_without_a_stored_success_is_not_classified(outcome):
    """`achieved` is a constant here only because `_write_repair_lesson` runs
    solely when the fix re-verified clean (`core/self_repair.py:485`). A row
    whose own `outcome` contradicts that is corrupt, hand-made, or from a future
    writer — and must not collect `achieved` for free."""
    assert writer_backfill_verdict(_self_repair_row(outcome=outcome)) is None


# ==========================================================================
# A settled axis is settled — presence, not truthiness.
# ==========================================================================
@pytest.mark.parametrize("stored", ["blocked", "", None, 0, False, "nonsense"])
def test_any_present_completion_state_is_left_alone(stored):
    """`to_dict` omits the key when the verdict is unset, so an absent key is
    the one honest encoding of "banked before the axis". Anything present was
    written on purpose, and re-deciding it would settle the axis twice."""
    assert writer_backfill_verdict(_self_build_row("success", completion_state=stored)) is None
