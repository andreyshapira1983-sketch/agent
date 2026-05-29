"""MVP-14.3 — Source Ranker / Evidence Trust Layer.

Verifier answers a binary question: did this citation resolve to evidence?
SourceRanker answers the next question: how much should this evidence be
trusted for this specific user question?

This first cut is deterministic and local. It does not call an LLM and does
not fetch the network. It scores existing Evidence records by:

  - source tier (test/file/user > fetched web page > search pointer > LLM)
  - freshness for time-sensitive questions
  - whether the evidence class can support realtime claims at all

The ranker feeds the Ranker-to-Output policy, so support diagnostics can
cap final confidence and move unsuitable realtime claims to Unverified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from core.evidence import Evidence, ProvenanceChain


SourceTier = Literal[
    "authoritative",
    "primary",
    "reputable",
    "general_web",
    "blog_or_forum",
    "search_pointer",
    "memory",
    "llm",
    "unknown",
]
FreshnessStatus = Literal[
    "not_time_sensitive",
    "fresh",
    "dated",
    "stale",
    "undated",
]
SupportLevel = Literal[
    "direct",
    "weak",
    "insufficient_for_realtime",
]


_TIER_SCORES: dict[SourceTier, float] = {
    "authoritative": 1.00,
    "primary": 0.95,
    "reputable": 0.82,
    "general_web": 0.68,
    "blog_or_forum": 0.50,
    "search_pointer": 0.30,
    "memory": 0.55,
    "llm": 0.20,
    "unknown": 0.10,
}

_FRESHNESS_SCORES: dict[FreshnessStatus, float] = {
    "not_time_sensitive": 1.00,
    "fresh": 1.00,
    "dated": 0.75,
    "stale": 0.45,
    "undated": 0.35,
}

_REPUTABLE_DOMAINS = frozenset({
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "coindesk.com",
    "coinmarketcap.com",
    "investing.com",
    "wikipedia.org",
    "wikibooks.org",
    "wikisource.org",
    "gutenberg.org",
    "archive.org",
    "openlibrary.org",
    "arxiv.org",
    "pubmed.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",
    "plato.stanford.edu",
    "developer.mozilla.org",
    "learn.microsoft.com",
    "rfc-editor.org",
    "docs.python.org",
})

_BLOG_OR_FORUM_DOMAINS = frozenset({
    "medium.com",
    "substack.com",
    "reddit.com",
    "quora.com",
    "wordpress.com",
    "blogspot.com",
})

_REALTIME_DOMAIN_HINTS = frozenset({
    "coinmarketcap.com",
    "coingecko.com",
    "nasdaq.com",
    "finance.yahoo.com",
    "investing.com",
    "openweathermap.org",
})

_REALTIME_TERMS = (
    "right now", "current", "currently", "latest", "today", "now",
    "price", "market", "stock", "quote", "exchange rate", "weather",
    "прямо сейчас", "сейчас", "текущ", "последн", "сегодня",
    "цена", "курс", "котиров", "погода", "биткоин", "bitcoin", "btc",
)


@dataclass(frozen=True)
class SourceRank:
    """Trust diagnosis for one Evidence record."""

    evidence_id: str
    kind: str
    source_id: str
    tier: SourceTier
    tier_score: float
    freshness_status: FreshnessStatus
    freshness_score: float
    support_level: SupportLevel
    final_score: float
    confidence_ceiling: float
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "source_id": self.source_id,
            "tier": self.tier,
            "tier_score": self.tier_score,
            "freshness_status": self.freshness_status,
            "freshness_score": self.freshness_score,
            "support_level": self.support_level,
            "final_score": self.final_score,
            "confidence_ceiling": self.confidence_ceiling,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class SourceRankingReport:
    """Ranking result for a provenance chain."""

    question: str
    realtime_required: bool
    ranks: tuple[SourceRank, ...] = field(default_factory=tuple)

    @property
    def best(self) -> SourceRank | None:
        if not self.ranks:
            return None
        return max(self.ranks, key=lambda r: r.final_score)

    def support_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rank in self.ranks:
            counts[rank.support_level] = counts.get(rank.support_level, 0) + 1
        return counts

    def to_log_payload(self) -> dict[str, Any]:
        best = self.best
        return {
            "realtime_required": self.realtime_required,
            "count": len(self.ranks),
            "best": best.to_dict() if best is not None else None,
            "support_counts": self.support_counts(),
            "ranks": [r.to_dict() for r in self.ranks],
        }


def is_realtime_question(question: str) -> bool:
    """Return True when a question asks for current/fresh data."""
    lowered = (question or "").casefold()
    return any(term in lowered for term in _REALTIME_TERMS)


def rank_chain(
    chain: ProvenanceChain,
    *,
    question: str = "",
    now: datetime | None = None,
) -> SourceRankingReport:
    realtime = is_realtime_question(question)
    ranks = tuple(
        rank_evidence(ev, question=question, now=now, realtime_required=realtime)
        for ev in chain.evidences
    )
    return SourceRankingReport(
        question=question,
        realtime_required=realtime,
        ranks=ranks,
    )


def rank_evidence(
    evidence: Evidence,
    *,
    question: str = "",
    now: datetime | None = None,
    realtime_required: bool | None = None,
) -> SourceRank:
    """Score one Evidence record for the current question."""
    realtime = is_realtime_question(question) if realtime_required is None else realtime_required
    tier, tier_reasons = _source_tier(evidence)
    fresh, fresh_reasons = _freshness_status(evidence, realtime_required=realtime, now=now)
    support, support_reasons = _support_level(evidence, tier=tier, realtime_required=realtime)

    tier_score = _TIER_SCORES[tier]
    freshness_score = _FRESHNESS_SCORES[fresh]
    base = min(max(float(evidence.confidence), 0.0), 1.0)
    final_score = base * tier_score * freshness_score
    confidence_ceiling = _confidence_ceiling(
        tier=tier,
        freshness=fresh,
        support=support,
        realtime_required=realtime,
    )
    final_score = min(final_score, confidence_ceiling)

    return SourceRank(
        evidence_id=evidence.id,
        kind=evidence.kind,
        source_id=evidence.source_id,
        tier=tier,
        tier_score=round(tier_score, 3),
        freshness_status=fresh,
        freshness_score=round(freshness_score, 3),
        support_level=support,
        final_score=round(final_score, 3),
        confidence_ceiling=round(confidence_ceiling, 3),
        reasons=tuple(tier_reasons + fresh_reasons + support_reasons),
    )


def _source_tier(evidence: Evidence) -> tuple[SourceTier, list[str]]:
    kind = evidence.kind
    if kind in {"user_explicit", "test_result"}:
        return "authoritative", [f"{kind} is authoritative for this run"]
    if kind == "tool_output" and evidence.obtained_via in {
        "market_price",
        "finance",
        "market_quote",
    }:
        return "primary", [f"{evidence.obtained_via} is structured market evidence"]
    if kind in {"file", "log_event", "shell_output"}:
        return "primary", [f"{kind} is primary local evidence"]
    if kind == "diff_preview":
        return "primary", ["diff preview is primary but not yet applied"]
    if kind == "web_search_hit":
        return "search_pointer", ["search results are pointers, not source material"]
    if kind == "memory":
        return "memory", ["memory needs provenance before high trust"]
    if kind == "llm_claim":
        return "llm", ["LLM prior knowledge is not external evidence"]
    if kind != "web_page":
        return "unknown", [f"{kind} has no dedicated rank rule"]

    domain = _domain_from_source_id(evidence.source_id)
    if not domain:
        return "general_web", ["web page has no parseable domain"]
    if _is_official_domain(domain):
        return "authoritative", [f"{domain} looks official"]
    if _domain_matches(domain, _REPUTABLE_DOMAINS):
        return "reputable", [f"{domain} is in reputable-domain list"]
    if _domain_matches(domain, _BLOG_OR_FORUM_DOMAINS) or "/blog" in evidence.source_id.lower():
        return "blog_or_forum", [f"{domain} looks like blog/forum content"]
    return "general_web", [f"{domain} is general web evidence"]


def _freshness_status(
    evidence: Evidence,
    *,
    realtime_required: bool,
    now: datetime | None,
) -> tuple[FreshnessStatus, list[str]]:
    if not realtime_required:
        return "not_time_sensitive", ["question is not time-sensitive"]

    # Local runtime observations are current for the run.
    if evidence.kind in {"test_result", "log_event", "shell_output"}:
        return "fresh", [f"{evidence.kind} was produced during this run"]

    fetched = _parse_dt(evidence.fetched_at)
    if fetched is None:
        return "undated", ["evidence has no parseable timestamp"]
    ref = now or datetime.now(timezone.utc)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    age_seconds = max(0.0, (ref - fetched).total_seconds())
    if age_seconds <= 24 * 3600:
        return "fresh", ["evidence timestamp is within 24h"]
    if age_seconds <= 7 * 24 * 3600:
        return "dated", ["evidence timestamp is within 7d"]
    return "stale", ["evidence timestamp is older than 7d"]


def _support_level(
    evidence: Evidence,
    *,
    tier: SourceTier,
    realtime_required: bool,
) -> tuple[SupportLevel, list[str]]:
    if realtime_required and not _supports_realtime(evidence):
        return (
            "insufficient_for_realtime",
            ["realtime question needs live/specialized source; this evidence is not enough"],
        )
    if tier in {"search_pointer", "llm", "unknown"}:
        return "weak", [f"{tier} cannot strongly verify a claim"]
    return "direct", ["source can directly support non-realtime claims"]


def _confidence_ceiling(
    *,
    tier: SourceTier,
    freshness: FreshnessStatus,
    support: SupportLevel,
    realtime_required: bool,
) -> float:
    if support == "insufficient_for_realtime":
        return 0.35
    if support == "weak":
        return 0.45
    if realtime_required and freshness in {"stale", "undated"}:
        return 0.55
    if tier == "blog_or_forum":
        return 0.65
    if tier == "general_web":
        return 0.75
    return 1.0


def _supports_realtime(evidence: Evidence) -> bool:
    if evidence.kind in {"test_result", "log_event", "shell_output"}:
        return True
    if evidence.kind == "tool_output" and evidence.obtained_via in {
        "market_price",
        "finance",
        "market_quote",
    }:
        return _has_realtime_timestamp(evidence)
    if evidence.kind != "web_page":
        return False
    domain = _domain_from_source_id(evidence.source_id)
    if not domain:
        return False
    return (
        _domain_matches(domain, _REALTIME_DOMAIN_HINTS)
        and _has_realtime_timestamp(evidence)
    )


def _has_realtime_timestamp(evidence: Evidence) -> bool:
    text = f"{evidence.claim}\n{evidence.excerpt}".casefold()
    timestamp_markers = (
        "last updated",
        "updated",
        "as of",
        "timestamp",
        "utc",
        "gmt",
        "published_at",
        "fetched_at",
        "обновлено",
        "по состоянию",
        "время",
    )
    if any(marker in text for marker in timestamp_markers):
        return True
    # Common ISO/date-time shapes. A fetched page timestamp alone is handled
    # by `fetched_at`; here we need the source/tool content itself to expose a
    # timestamp for the market value.
    if re.search(r"\b20\d{2}-\d{2}-\d{2}[t\s]\d{2}:\d{2}", text):
        return True
    return False


def _domain_from_source_id(source_id: str) -> str:
    raw = source_id
    for prefix in ("web_page:", "web_search:"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    parsed = urlparse(raw)
    host = parsed.netloc or parsed.path.split("/", 1)[0]
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_official_domain(domain: str) -> bool:
    if domain.endswith(".gov") or domain.endswith(".edu"):
        return True
    if domain in {
        "docs.python.org",
        "developer.mozilla.org",
        "learn.microsoft.com",
        "rfc-editor.org",
    }:
        return True
    return domain.startswith(("docs.", "developer.", "api.", "help.", "support."))


def _domain_matches(domain: str, known: frozenset[str]) -> bool:
    return any(domain == item or domain.endswith("." + item) for item in known)


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
