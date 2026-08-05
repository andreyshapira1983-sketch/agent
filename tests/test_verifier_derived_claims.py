"""MIR-060 — `verified` means the citation resolved, not that the claim follows.

A claim whose truth is a COMPUTATION over the cited excerpt — a count, a sum,
a comparison, an inference — is marked `verified` because the citation
resolved. The verifier never evaluates the claim against the excerpt, so it
cannot tell a correct derivation from a false one.

This matters beyond display. `verified_chunks` drives the quality score
(MIR-002), the `outcome` derivation, `decide_usage_eligibility` (where
`verified_chunks > 0` is an admission condition), procedural credit and the
confidence gate. Measured on the live store 2026-08-05: 271 `verified` verdicts
across 94 episodes, 41 episodes resting on them, 16 of those admitted as
reusable experience. A false derived claim does not merely reach the operator —
it can make its episode look well-supported enough to be replayed.

## Why these tests are `xfail(strict=True)` and not ordinary failures

The defect is registered, reproduced and OPEN. Its fix direction is recorded in
`docs/audit/MASTER_ISSUE_REGISTRY.md` as a fork of three, explicitly "not a
recommendation": run the entailment check on resolved citations too (puts a
model on the hot path and makes the verdict depend on it), widen the
deterministic structured-fact path (sound where it applies, silent where it
does not), or give derived claims their own verdict (honest, but every consumer
of `verified_chunks` must then be re-examined). Choosing is an operator
decision, not a detail of writing the test.

Four of the five markers came off on 2026-08-05 when direction (b) landed and
the suite went red with `XPASS(strict)` — the instrument doing its job. What
remains marked is the entailment check, which only direction (a) reaches.

So these tests state the CORRECT behaviour. They are
not skips: a skip whose reason describes code behaviour is a defect report
nobody filed (notebook §23). `strict=True` means the day the behaviour changes
— by a fix or by accident — the suite goes red and someone must come back
here and delete the marker. The alternative, a characterization test asserting
today's wrong answer, would be green forever and would teach the next reader
that this is intended.

The seventh row and `test_a_literal_quotation_stays_verified` carry NO marker:
they pass today and must keep passing. They are closure criteria 2 and 3 — the
fix must not buy correctness by refusing to judge.
"""
from __future__ import annotations

from typing import Any

import pytest

from core.evidence import ProvenanceChain, make_evidence
from core.verifier_core import verify

_EXCERPT = "alpha=1\nbeta=2\ngamma=3\n"
_MIR_060 = "MIR-060: verdict follows citation resolution, not the claim's truth"


def _chain(excerpt: str = _EXCERPT) -> ProvenanceChain:
    chain = ProvenanceChain()
    chain.add(make_evidence(
        kind="file",
        source_id="vals.txt",
        obtained_via="tool",
        claim="contents of vals.txt",
        excerpt=excerpt,
    ))
    return chain


def _verdict(claim: str, *, excerpt: str = _EXCERPT, llm: Any = None) -> str:
    """The verdict for a single claim citing the one file in the chain."""
    report = verify(
        answer=f"{claim} [file:vals.txt]",
        chain=_chain(excerpt),
        llm=llm,
        expects_contract_headers=False,
    )
    assert report.chunks, "the answer produced no claim chunk to judge"
    return report.chunks[0].verdict


# --------------------------------------------------------------------------
# False derived claims — each cites a source that resolves, and each is wrong
# --------------------------------------------------------------------------

@pytest.mark.parametrize("claim", [
    "The three values sum to 99",      # arithmetic over the excerpt: 6, not 99
    "The file defines 7 keys",         # a count: three keys, not seven
    "gamma is smaller than alpha",     # a comparison: 3 > 1
])
def test_a_false_derived_claim_is_not_verified(claim: str):
    """Resolution is not evaluation.

    All three cite `vals.txt`, which is in the chain and contains exactly the
    numbers that refute them. Each used to score `verified` — identically to
    the true derivation and to a literal quotation.

    FIXED 2026-08-05 by MIR-060 direction (b): `core/claim_arithmetic.py`
    computes the sum, the count and the comparison from the excerpt and a
    refutation both demotes the claim and attaches its working. The strict
    marker came off the day the behaviour changed, which is what it is for.
    """
    assert _verdict(claim) != "verified", (
        f"{claim!r} is false against the cited excerpt and still scored "
        "`verified` — the verdict tracks whether the citation resolved"
    )


# --------------------------------------------------------------------------
# The two that must NOT regress — closure criteria 2 and 3
# --------------------------------------------------------------------------

def test_a_true_derived_claim_stays_verified():
    """The fix must not buy correctness by refusing to judge (criterion 2)."""
    assert _verdict("The three values sum to 6") == "verified"


def test_a_literal_quotation_stays_verified():
    """A quotation must keep the path it uses today (criterion 3)."""
    assert _verdict("The file contains beta=2") == "verified"


# --------------------------------------------------------------------------
# The containment band: the one content check that exists, and its blind spot
# --------------------------------------------------------------------------

def test_a_statistical_claim_without_its_figure_is_held_back():
    """The narrow band that does bite today, pinned so a fix keeps it."""
    assert _verdict("The average value is 12") != "verified"
    assert _verdict("Coverage rose 40%") != "verified"


def test_an_unrelated_number_cannot_certify_an_average():
    """`port=12` is not the average of 1, 2 and 3.

    The containment test asks whether the figure's characters appear anywhere
    in the excerpt. It does not compute the average and has no notion of which
    number belongs to which quantity, so an unrelated `port=12` flips the
    verdict from held-back to `verified` — closure criterion 4.

    FIXED 2026-08-05: the arithmetic gate computes the average as 2 and refutes
    the claim before containment is consulted, so an unrelated number can no
    longer certify it.
    """
    verdict = _verdict("The average value is 12",
                       excerpt=_EXCERPT + "port=12\n")
    assert verdict != "verified", (
        "an unrelated `port=12` certified an average it has nothing to do with"
    )


# --------------------------------------------------------------------------
# The machinery that could catch this exists and is unreachable
# --------------------------------------------------------------------------

class _RefusesEverything:
    """An LLM that answers NO to every entailment question, and counts asks."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return "NO"


@pytest.mark.xfail(strict=True, reason=_MIR_060 + " (resolution short-circuits evaluation)")
def test_the_semantic_check_is_consulted_for_a_resolved_citation():
    """`_find_semantic_support` is reachable only when nothing matched.

    So for exactly the claims that need evaluating — the ones that DID cite a
    real source — the entailment check never runs. Measured: a model that
    refuses every claim changes nothing, and is not asked once.
    """
    llm = _RefusesEverything()
    verdict = _verdict("The three values sum to 99", llm=llm)

    assert llm.calls, (
        "the entailment check was never consulted for a claim whose citation "
        "resolved — resolution short-circuits evaluation"
    )
    assert verdict != "verified"
