"""Integration tests for the Verifier's structured-facts fallback.

The Verifier matches `[tool:<name>]` citations to tool_output Evidence
records, and additionally accepts uncited claims that derive from
structured tool returns via deterministic locale-aware matching.
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import (
    verify,
)


def _chain_with_current_time() -> ProvenanceChain:
    excerpt = (
        "{'iso_utc': '2026-06-03T12:47:35+00:00', "
        "'weekday': 'Wednesday', 'year': 2026, 'month': 6, 'day': 3, "
        "'tz_name': 'Europe/Moscow', 'unix': 1780000000}"
    )
    chain = ProvenanceChain()
    chain.add(make_evidence(
        kind="tool_output",
        source_id="tool_output:current_time",
        obtained_via="current_time",
        claim="Tool current_time returned a result",
        excerpt=excerpt,
    ))
    return chain


class TestStructuredFallbackForUncitedClaims:
    def test_russian_paraphrase_matches_english_source_without_citation(self):
        chain = _chain_with_current_time()
        report = verify(
            answer="Сегодня среда, 3 июня 2026 года.",
            chain=chain,
        )
        # Without the structured fallback this would be `unverified`.
        # With it, the Verifier accepts the russian paraphrase.
        assert report.verified_chunks == 1, report.to_log_payload()
        assert report.unverified_chunks == 0
        # Synthesised citation should appear in the annotated output.
        assert "[verified:tool:current_time]" in report.annotated_answer

    def test_unrelated_claim_still_unverified(self):
        chain = _chain_with_current_time()
        report = verify(
            answer="Кошки обычно спят 16 часов в сутки.",
            chain=chain,
        )
        assert report.verified_chunks == 0
        assert report.unverified_chunks == 1


class TestStructuredFallbackForCitedButUnmatched:
    def test_misnamed_citation_recovered_by_structured_match(self):
        # Synthesizer cited [tool:date_now] (wrong name) but the claim
        # text still derives from the current_time evidence's structured
        # facts. The structured fallback must rescue this before the
        # NLI tier (no llm passed) and produce a verified verdict.
        chain = _chain_with_current_time()
        report = verify(
            answer="Сегодня среда, 3 июня 2026 года [tool:date_now].",
            chain=chain,
        )
        assert report.verified_chunks == 1, report.to_log_payload()
        assert report.cited_but_unmatched_chunks == 0
