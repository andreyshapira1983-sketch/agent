"""Statistical-claim strict verification.

A claim with a percentage / range / multiplier / pricing must have its
ACTUAL FIGURE substring-matched in the cited evidence's excerpt — a
search-hit that only proves the topic exists is no longer enough.

This pins the new verdict bucket
`topic_supported_but_claim_unverified` and the helper detectors
`is_statistical_claim` / `extract_statistical_figures`.
"""
from __future__ import annotations

import pytest

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import (
    extract_statistical_figures,
    is_statistical_claim,
    verify,
)


# ===========================================================================
# Pure detector unit tests
# ===========================================================================

class TestStatisticalDetectors:
    @pytest.mark.parametrize("text", [
        "66% of developers ship without tests",
        "Conversion rose to 12 %",
        "Most developers test in production",
        "majority prefer dark mode",
        "Solution takes 2-4 weeks to build",
        "Solution takes 2–4 weeks",  # en-dash
        "Pricing: $199 per seat",
        "It's 2x faster than v1",
        "10x growth this year",
        "В среднем 3 итерации",
        "большинство стартапов не доживает",
        "В 2 раза быстрее",
        "45 процентов команд",
        "199 USD per month",
    ])
    def test_trigger_fires(self, text):
        assert is_statistical_claim(text) is True

    @pytest.mark.parametrize("text", [
        "The answer is yes.",
        "This module imports json and reads stdin.",
        "Section 3 covers tests.",  # bare number, no trigger
        "Part 2 of the report.",
    ])
    def test_trigger_silent(self, text):
        assert is_statistical_claim(text) is False

    def test_extract_figures_collects_all(self):
        figs = extract_statistical_figures(
            "66% of developers ship in 2-4 weeks; pricing is $199"
        )
        # All three figures present, dedup applied, order preserved.
        joined = ",".join(figs)
        assert "66%" in joined
        assert "2-4" in joined or "2 - 4" in joined
        assert "$199" in joined or "$ 199" in joined

    def test_extract_figures_returns_empty_for_qualifier_only(self):
        figs = extract_statistical_figures("Most developers test their code")
        assert figs == []

    def test_extract_figures_returns_empty_for_non_statistical(self):
        figs = extract_statistical_figures("This is a plain sentence.")
        assert figs == []


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


def _page(url: str, body: str):
    return make_evidence(
        kind="web_page",
        source_id=f"web_page:{url}",
        obtained_via="web_fetch",
        claim=f"Fetched {url}",
        excerpt=body,
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
    def test_percentage_claim_demoted_when_number_absent(self):
        # Search hit's excerpt mentions the topic but not the number.
        chain = ProvenanceChain()
        chain.add(_hit(
            "developer testing habits",
            "developer testing habits remain a hot topic this year",
        ))
        answer = _well_formed(
            "66% of developers ship without tests "
            "[web:developer testing habits]."
        )
        report = verify(answer=answer, chain=chain)

        assert report.topic_supported_but_claim_unverified_chunks == 1
        assert report.verified_chunks == 0
        # Annotated answer carries the visible markers.
        assert "[topic-only:web:" in report.annotated_answer
        assert "[claim-figure-unverified]" in report.annotated_answer

    def test_percentage_claim_verified_when_number_present_in_excerpt(self):
        chain = ProvenanceChain()
        chain.add(_hit(
            "developer testing habits",
            "Survey shows 66% of developers ship without tests",
        ))
        answer = _well_formed(
            "66% of developers ship without tests "
            "[web:developer testing habits]."
        )
        report = verify(answer=answer, chain=chain)

        assert report.verified_chunks == 1
        assert report.topic_supported_but_claim_unverified_chunks == 0
        assert "[verified:web:" in report.annotated_answer
        assert "[claim-figure-unverified]" not in report.annotated_answer

    def test_range_claim_demoted_when_excerpt_lacks_range(self):
        chain = ProvenanceChain()
        chain.add(_hit(
            "saas time to market",
            "building a saas product takes time and effort",
        ))
        answer = _well_formed(
            "A solution typically takes 2-4 weeks to ship "
            "[web:saas time to market]."
        )
        report = verify(answer=answer, chain=chain)

        assert report.topic_supported_but_claim_unverified_chunks == 1
        assert report.verified_chunks == 0

    def test_range_claim_passes_with_endash_in_source(self):
        # Claim uses ASCII "-", source uses en-dash "–" — the
        # normaliser must fold both before substring comparison.
        chain = ProvenanceChain()
        chain.add(_hit(
            "saas time to market",
            "MVPs typically ship in 2–4 weeks of focused work",
        ))
        answer = _well_formed(
            "A solution typically takes 2-4 weeks to ship "
            "[web:saas time to market]."
        )
        report = verify(answer=answer, chain=chain)

        assert report.verified_chunks == 1
        assert report.topic_supported_but_claim_unverified_chunks == 0

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

    def test_qualifier_only_with_web_page_evidence_passes(self):
        # Same qualifier-only claim, but cited evidence is a web_page
        # (page-level, authoritative). Strict mode does not demote.
        chain = ProvenanceChain()
        chain.add(_page(
            "https://example.com/report",
            "the report concludes most developers prefer staged rollouts",
        ))
        answer = _well_formed(
            "Most developers prefer staged rollouts "
            "[web:https://example.com/report]."
        )
        report = verify(answer=answer, chain=chain)

        assert report.verified_chunks == 1
        assert report.topic_supported_but_claim_unverified_chunks == 0

    def test_nonstatistical_claim_unaffected(self):
        # No statistical trigger — strict mode is a no-op even with a
        # search-hit citation.
        chain = ProvenanceChain()
        chain.add(_hit(
            "react basics",
            "React is a JavaScript library for building user interfaces",
        ))
        answer = _well_formed(
            "React is a UI library [web:react basics]."
        )
        report = verify(answer=answer, chain=chain)

        assert report.verified_chunks == 1
        assert report.topic_supported_but_claim_unverified_chunks == 0

    def test_two_citations_one_passes_chunk_is_verified(self):
        # Mixed chunk: one cite passes strict, another doesn't.
        # `any_matched` wins, but the failed cite is still rewritten as
        # topic-only so the user can see which source actually backed
        # the number.
        chain = ProvenanceChain()
        chain.add(_hit(
            "with-number",
            "Our 2025 study found 66% of teams ship daily",
        ))
        chain.add(_hit(
            "topic-only",
            "engineering productivity remains hard to measure",
        ))
        answer = _well_formed(
            "66% of teams ship daily "
            "[web:with-number] [web:topic-only]."
        )
        report = verify(answer=answer, chain=chain)

        assert report.verified_chunks == 1
        assert report.topic_supported_but_claim_unverified_chunks == 0
        # The strict-passing cite became [verified:...]; the other
        # became [topic-only:...] in the same annotated chunk.
        assert "[verified:web:with-number]" in report.annotated_answer
        assert "[topic-only:web:topic-only]" in report.annotated_answer

    def test_pricing_claim_demoted_when_amount_absent(self):
        chain = ProvenanceChain()
        chain.add(_hit(
            "saas pricing",
            "saas pricing models are evolving with usage-based billing",
        ))
        answer = _well_formed(
            "Pricing starts at $199 per seat [web:saas pricing]."
        )
        report = verify(answer=answer, chain=chain)
        assert report.topic_supported_but_claim_unverified_chunks == 1
        assert report.verified_chunks == 0

    def test_log_payload_includes_topic_supported_count(self):
        chain = ProvenanceChain()
        chain.add(_hit(
            "x",
            "topic mentioned but no figures",
        ))
        answer = _well_formed("66% of users churn [web:x].")
        report = verify(answer=answer, chain=chain)
        payload = report.to_log_payload()
        assert payload["topic_supported_but_claim_unverified_chunks"] == 1
        assert "topic_supported_but_claim_unverified" in payload["verdicts"]
