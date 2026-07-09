"""Integration tests for the Verifier's structured-facts fallback.

The Verifier matches `[tool:<name>]` citations to tool_output Evidence
records, and additionally accepts uncited claims that derive from
structured tool returns via deterministic locale-aware matching.
"""
from __future__ import annotations

import pytest

from core.evidence import ProvenanceChain, make_evidence
from core.verifier import (
    CITATION_PREFIXES,
    parse_citations,
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


class TestToolCitationPrefix:
    def test_tool_prefix_in_grammar(self):
        assert CITATION_PREFIXES.get("tool") == "tool_output"

    def test_tool_citation_parses(self):
        cits = parse_citations("Today is Wednesday [tool:current_time].")
        assert len(cits) == 1
        assert cits[0].prefix == "tool"
        assert cits[0].body == "current_time"
        assert cits[0].expected_kind == "tool_output"

    def test_tool_citation_resolves_via_substring(self):
        chain = _chain_with_current_time()
        report = verify(
            answer="Today is Wednesday, June 3, 2026 [tool:current_time].",
            chain=chain,
        )
        assert report.verified_chunks == 1
        assert report.cited_but_unmatched_chunks == 0
        assert report.unverified_chunks == 0
        # Annotated answer rewrites the citation as verified.
        assert "[verified:tool:current_time]" in report.annotated_answer


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


class TestStructuredFallbackDoesNotOverreach:
    def test_empty_chain_no_match(self):
        chain = ProvenanceChain()
        report = verify(
            answer="Сегодня среда, 3 июня 2026 года.",
            chain=chain,
        )
        assert report.verified_chunks == 0
        assert report.chain_was_empty is True

    def test_only_non_tool_evidence_no_structured_match(self):
        chain = ProvenanceChain()
        chain.add(make_evidence(
            kind="file",
            source_id="file:README.md",
            obtained_via="file_read",
            claim="README contents",
            excerpt="The autonomous agent project.",
        ))
        report = verify(
            answer="Сегодня среда, 3 июня 2026 года.",
            chain=chain,
        )
        # No tool_output evidence -> no structured rescue.
        assert report.verified_chunks == 0
        assert report.unverified_chunks == 1
