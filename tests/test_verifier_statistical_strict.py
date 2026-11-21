"""Statistical-claim strict verification.

A claim with a percentage / range / multiplier / pricing must have its
ACTUAL FIGURE substring-matched in the cited evidence's excerpt — a
search-hit that only proves the topic exists is no longer enough.

This pins the new verdict bucket
`topic_supported_but_claim_unverified` and the helper detectors
`is_statistical_claim` / `extract_statistical_figures`.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import (
    verify,
)

# ===========================================================================
# Strict-gate end-to-end via verify()
# ===========================================================================

def _hit(query: str, snippet: str):
    return make_evidence(
        kind="web_search_hit",
        source_id=f"web_search:{query}",
        obtained_via="web_search",
        claim=f"Search '{query}' returned snippet",
        excerpt=snippet,
    )


def _well_formed(facts: str) -> str:
    """Wrap a single Facts line in the minimal Output Contract shape so
    the verifier doesn't flag the answer as `malformed_output` and the
    section-header machinery is exercised the way real runs hit it."""
    return (
        "Conclusion: see facts.\n"
        f"Facts: {facts}\n"
        "Sources: see citations\n"
        "Confidence: low\n"
        "Unverified: nothing\n"
        "Safety: ok"
    )


class TestStatisticalStrictGate:
    def test_qualifier_only_search_hit_demoted(self):
        # "Most" / "majority" with no number — a search-hit cannot ground
        # a population-level claim no matter what its excerpt says.
        chain = ProvenanceChain()
        chain.add(_hit(
            "developer testing",
            "developer testing covers everything from unit to e2e",
        ))
        answer = _well_formed(
            "Most developers ship without tests "
            "[web:developer testing]."
        )
        report = verify(answer=answer, chain=chain)

        assert report.topic_supported_but_claim_unverified_chunks == 1
        assert report.verified_chunks == 0
