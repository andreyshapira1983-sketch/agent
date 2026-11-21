"""Tests for Ranker-to-Output policy."""

from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.output_policy import apply_ranker_output_policy
from core.source_ranker import rank_chain


def _chain_with_page(source: str, excerpt: str = "Bitcoin price is 123 USD") -> ProvenanceChain:
    chain = ProvenanceChain()
    chain.add(
        make_evidence(
            kind="web_page",
            source_id=source,
            obtained_via="web_fetch",
            claim="Fetched market page",
            excerpt=excerpt,
            fetched_at="2026-05-28T11:59:00+00:00",
        )
    )
    return chain


def test_realtime_insufficient_source_downgrades_verified_claims_and_confidence() -> None:
    chain = _chain_with_page("web_page:https://example.com/bitcoin")
    ranking = rank_chain(chain, question="Какая цена Bitcoin прямо сейчас?")
    answer = (
        "Conclusion: Цена Bitcoin 123 USD. [verified:web:https://example.com/bitcoin]\n"
        "Facts:\n"
        "- Страница была открыта. [verified:web:https://example.com/bitcoin]\n"
        "Sources:\n"
        "1. web:https://example.com/bitcoin\n"
        "Confidence: high\n"
        "Unverified: nothing\n"
        "Safety: nothing\n"
    )

    result = apply_ranker_output_policy(
        answer=answer,
        ranking=ranking,
        question="Какая цена Bitcoin прямо сейчас?",
    )

    assert result.applied is True
    assert result.confidence_ceiling == "low"
    assert result.downgraded_realtime_claims == 2
    # Body edits stay in the answer: they are corrections to the claims.
    assert "Confidence: low" in result.answer
    assert "[unverified:insufficient_for_realtime]" in result.answer
    # The warning is REPORTED, not merged here. It is prose about the answer,
    # so `ResponseDraft` composes it onto whatever body survives — merging it
    # at this layer is what let a later body rewrite delete it.
    assert any(
        "недостаточны для подтверждения realtime-значения" in w
        for w in result.warnings
    )
