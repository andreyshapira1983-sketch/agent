"""MVP-14.3 — unit tests for core/source_ranker.py."""

from __future__ import annotations

from datetime import datetime, timezone

from core.evidence import ProvenanceChain, make_evidence
from core.source_ranker import (
    is_realtime_question,
    rank_chain,
    rank_evidence,
)


NOW = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)


def _ev(
    kind: str,
    source: str,
    *,
    fetched_at: str | None = None,
    conf: float | None = None,
    excerpt: str = "excerpt",
    obtained_via: str = "test",
):
    return make_evidence(
        kind=kind,  # type: ignore[arg-type]
        source_id=source,
        obtained_via=obtained_via,
        claim="claim",
        excerpt=excerpt,
        fetched_at=fetched_at,
        confidence=conf,
    )


def test_realtime_question_detector_handles_market_language():
    assert is_realtime_question("Какая цена Bitcoin прямо сейчас?")
    assert is_realtime_question("latest BTC price today")
    assert not is_realtime_question("Кто написал README проекта?")


def test_search_hit_is_weak_pointer():
    rank = rank_evidence(
        _ev("web_search_hit", "web_search:bitcoin price"),
        question="Какая цена Bitcoin прямо сейчас?",
        now=NOW,
    )
    assert rank.tier == "search_pointer"
    assert rank.support_level == "insufficient_for_realtime"
    assert rank.confidence_ceiling == 0.35
    assert rank.final_score <= 0.35


def test_fetched_general_web_page_is_not_enough_for_realtime():
    rank = rank_evidence(
        _ev(
            "web_page",
            "web_page:https://example.com/bitcoin",
            fetched_at="2026-05-28T11:30:00+00:00",
        ),
        question="Какая цена Bitcoin прямо сейчас?",
        now=NOW,
    )
    assert rank.freshness_status == "fresh"
    assert rank.support_level == "insufficient_for_realtime"
    assert rank.final_score <= 0.35


def test_realtime_market_domain_without_value_timestamp_is_not_live_source():
    rank = rank_evidence(
        _ev(
            "web_page",
            "web_page:https://coinmarketcap.com/currencies/bitcoin/",
            fetched_at="2026-05-28T11:59:00+00:00",
            excerpt="Bitcoin price is 123 USD",
        ),
        question="Какая цена BTC сейчас?",
        now=NOW,
    )
    assert rank.tier == "reputable"
    assert rank.freshness_status == "fresh"
    assert rank.support_level == "insufficient_for_realtime"
    assert rank.final_score <= 0.35


def test_realtime_market_domain_can_directly_support_realtime_with_timestamp():
    rank = rank_evidence(
        _ev(
            "web_page",
            "web_page:https://coinmarketcap.com/currencies/bitcoin/",
            fetched_at="2026-05-28T11:59:00+00:00",
            excerpt="Bitcoin price is 123 USD. Last updated 2026-05-28 11:59 UTC.",
        ),
        question="Какая цена BTC сейчас?",
        now=NOW,
    )
    assert rank.tier == "reputable"
    assert rank.freshness_status == "fresh"
    assert rank.support_level == "direct"
    assert rank.final_score > 0.5


def test_stale_realtime_source_gets_confidence_ceiling():
    rank = rank_evidence(
        _ev(
            "web_page",
            "web_page:https://coinmarketcap.com/currencies/bitcoin/",
            fetched_at="2026-01-01T00:00:00+00:00",
            excerpt="Bitcoin price is 123 USD. Last updated 2026-01-01 00:00 UTC.",
        ),
        question="Какая цена BTC сейчас?",
        now=NOW,
    )
    assert rank.freshness_status == "stale"
    assert rank.support_level == "direct"
    assert rank.confidence_ceiling == 0.55
    assert rank.final_score <= 0.55


def test_structured_market_tool_with_timestamp_can_support_realtime():
    rank = rank_evidence(
        _ev(
            "tool_output",
            "market_price:BTC-USD",
            fetched_at="2026-05-28T11:59:00+00:00",
            excerpt="symbol=BTC currency=USD price=123 timestamp=2026-05-28T11:59:00Z",
            obtained_via="market_price",
        ),
        question="Какая цена BTC сейчас?",
        now=NOW,
    )
    assert rank.tier == "primary"
    assert rank.support_level == "direct"
    assert rank.final_score > 0.5


def test_official_docs_rank_above_blog_for_non_realtime_question():
    docs = rank_evidence(
        _ev("web_page", "web_page:https://docs.python.org/3/library/json.html"),
        question="Как работает json в Python?",
        now=NOW,
    )
    blog = rank_evidence(
        _ev("web_page", "web_page:https://medium.com/some-post"),
        question="Как работает json в Python?",
        now=NOW,
    )
    assert docs.tier == "authoritative"
    assert blog.tier == "blog_or_forum"
    assert docs.final_score > blog.final_score


def test_rank_chain_reports_best_and_support_counts():
    chain = ProvenanceChain()
    chain.add(_ev("web_search_hit", "web_search:python json"))
    chain.add(_ev("file", "file:README.md"))
    report = rank_chain(chain, question="Что написано в README?", now=NOW)
    assert report.best is not None
    assert report.best.kind == "file"
    assert report.support_counts()["direct"] == 1
    assert report.support_counts()["weak"] == 1
    payload = report.to_log_payload()
    assert payload["count"] == 2
    assert payload["best"]["kind"] == "file"
