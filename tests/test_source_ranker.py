"""MVP-14.3 — unit tests for core/source_ranker.py."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.evidence import make_evidence
from core.source_ranker import (
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


@pytest.mark.parametrize(
    ("url", "tier"),
    [
        ("https://www.anthropic.com/news/claude", "reputable"),
        ("https://nvidianews.nvidia.com/news/open-agent-safety-platform", "reputable"),
        ("https://www.bleepingcomputer.com/news/security/carbonato/", "reputable"),
        ("https://thehackernews.com/2026/09/carbonato-botnet.html", "reputable"),
        ("https://www.threatdown.com/blog/carbonato/", "reputable"),
        ("https://techcrunch.com/2026/09/01/rl-environments/", "reputable"),
        ("https://arstechnica.com/ai/2026/09/story/", "reputable"),
        ("https://www.semanticscholar.org/paper/abc", "reputable"),
        ("https://pypi.org/project/ruff/", "reputable"),
        ("https://stackoverflow.com/questions/1/how", "blog_or_forum"),
        ("https://news.ycombinator.com/item?id=1", "blog_or_forum"),
        ("https://www.linkedin.com/posts/someone_report", "blog_or_forum"),
        ("https://aitraining.jobs/roles/agentic-eval", "blog_or_forum"),
        ("https://github.com/sheeeng/nvidia-openshell", "general_web"),
        ("https://huggingface.co/someone/model", "general_web"),
        ("https://docs.claude.com/en/docs/intro", "authoritative"),
        ("https://www.cisa.gov/news-events/alerts", "authoritative"),
    ],
)
def test_vetted_domains_get_their_tier(url: str, tier: str):
    """Operator-vetted sites rank by kind: primary sources high, forums and aggregators low.

    A whole-site rule cannot tell an official GitHub org from a fork, so github.com stays general.
    """
    rank = rank_evidence(_ev("web_page", f"web_page:{url}"), question="Что нового?", now=NOW)
    assert rank.tier == tier
