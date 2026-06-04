"""Tests for Ranker-to-Output policy."""

from __future__ import annotations

from datetime import datetime, timezone

from core.evidence import ProvenanceChain, make_evidence
from core.output_policy import apply_ranker_output_policy
from core.source_ranker import rank_chain

# Anchor "now" to the fixed evidence timestamps so freshness-based assertions
# stay deterministic regardless of the real wall-clock date.
_FROZEN_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)


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
    assert "Confidence: low" in result.answer
    assert "[unverified:insufficient_for_realtime]" in result.answer
    assert "недостаточны для подтверждения realtime-значения" in result.answer


def test_realtime_live_source_keeps_high_confidence() -> None:
    chain = _chain_with_page(
        "web_page:https://coinmarketcap.com/currencies/bitcoin/",
        excerpt="Bitcoin price is 123 USD. Last updated 2026-05-28 11:59 UTC.",
    )
    ranking = rank_chain(chain, question="latest BTC price today", now=_FROZEN_NOW)
    answer = (
        "Conclusion: BTC is 123 USD. [verified:web:https://coinmarketcap.com/currencies/bitcoin/]\n"
        "Facts:\n"
        "- The source has a market timestamp. [verified:web:https://coinmarketcap.com/currencies/bitcoin/]\n"
        "Sources:\n"
        "1. web:https://coinmarketcap.com/currencies/bitcoin/\n"
        "Confidence: high\n"
        "Unverified: nothing\n"
        "Safety: nothing\n"
    )

    result = apply_ranker_output_policy(
        answer=answer,
        ranking=ranking,
        question="latest BTC price today",
    )

    assert result.applied is False
    assert result.answer == answer


def test_replan_exhausted_warning_is_merged_into_unverified() -> None:
    chain = ProvenanceChain()
    ranking = rank_chain(chain, question="Проверь страницу")
    answer = (
        "Conclusion: Не удалось открыть часть источников. [general-knowledge]\n"
        "Facts:\n"
        "- Попытка проверки остановилась. [general-knowledge]\n"
        "Sources:\n"
        "1. general-knowledge\n"
        "Confidence: low\n"
        "Unverified: nothing\n"
        "Safety: nothing\n"
    )

    result = apply_ranker_output_policy(
        answer=answer,
        ranking=ranking,
        question="Проверь страницу",
        replan_exhausted=True,
    )

    assert result.applied is True
    assert "исчерпания replan-бюджета" in result.answer
