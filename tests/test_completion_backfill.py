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


def test_self_repair_row_is_achieved_because_a_lesson_means_it_verified_clean():
    assert writer_backfill_verdict(_self_repair_row()) == "achieved"


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
