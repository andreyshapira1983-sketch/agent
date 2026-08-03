"""MIR-028 — why `verified_chunks > 0` coexists with `chain_was_empty=True`.

The live observation (LPF-006: `verified_chunks=4`, `chain_was_empty=True`)
looked like a contradiction, and the registry held two hypotheses: tool-error
chunks counted, or the counter computed before the chain check. Both are
wrong. The pinned mechanism (2026-08-03):

    `verify()` records `chain_empty = len(chain) == 0` FIRST, and only then
    injects a synthetic `user_explicit` evidence built from the operator's
    question. A chunk citing `[user:current_turn]` verifies against that
    injected evidence, while the report honestly says the *gathered* chain
    was empty. The two fields answer different questions.

These are CHARACTERIZATION tests: they lock the mechanism so the explanation
stays executable, they do not endorse the policy. Whether a chunk supported
only by the operator's own words may count `verified` (the analogue of issue
#119, where `dialogue_supported` is never `verified`) is an operator
classification call — the registry entry for MIR-028 carries that question.
If the operator rules the other way, these tests are the spec of what must
change, and their assertions flip with the ruling.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain
from core.verifier import verify


def test_a_user_turn_citation_is_user_asserted_never_verified():
    """The operator's ruling (2026-08-03): user words confirm only that the
    user SAID it — they never confirm the content's objective truth. A chunk
    supported solely by `[user:current_turn]` is `user_asserted`, and the
    report says honestly that nothing was verified."""
    report = verify(
        answer=(
            "**Facts:**\n"
            "- The operator asked for seventeen times twenty-three [user:current_turn].\n"
            "- A numeric result was requested [user:current_turn].\n"
        ),
        chain=ProvenanceChain(),
        user_question="Compute seventeen times twenty-three and give a numeric result.",
    )
    assert report.chain_was_empty is True
    assert report.verified_chunks == 0
    assert report.user_asserted_chunks == 2
    assert report.fully_unverified is True


def test_a_plain_echo_without_citation_does_not_verify():
    """The injection alone is not a free pass: restating the question without
    citing the user turn stays unverified, so MIR-028 is specifically about
    `[user:current_turn]` citations, not about any echo."""
    report = verify(
        answer=(
            "**Conclusion:**\nThe operator asked to compute seventeen times twenty-three.\n"
            "**Facts:**\n- The request mentions seventeen times twenty-three.\n"
        ),
        chain=ProvenanceChain(),
        user_question="Compute seventeen times twenty-three and give a numeric result.",
    )
    assert report.chain_was_empty is True
    assert report.verified_chunks == 0
    assert report.fully_unverified is True


def test_without_a_user_question_nothing_verifies_against_an_empty_chain():
    """Remove the injection and the seeming contradiction is impossible:
    an empty chain plus no user turn cannot produce a verified chunk."""
    report = verify(
        answer=(
            "**Facts:**\n"
            "- The operator asked for seventeen times twenty-three [user:current_turn].\n"
        ),
        chain=ProvenanceChain(),
        user_question=None,
    )
    assert report.chain_was_empty is True
    assert report.verified_chunks == 0
    assert report.fully_unverified is True
