"""Source Registry and extracted claims.

Evidence is per-run proof. SourceRegistry is the catalog view over that proof:
what source was used, what type it is, how much trust it has, and which
claims were extracted from it.

This module is deliberately local and deterministic. It does not parse PDFs,
books, videos, or GitHub by itself; those ingestion layers can add sources and
claims here without changing the agent loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Literal
from urllib.parse import urlparse

from core.evidence import Evidence, ProvenanceChain
from core.ids import new_id
from core.source_ranker import SourceRank, SourceRankingReport


SourceType = Literal[
    "book",
    "pdf",
    "article",
    "documentation",
    "video",
    "podcast",
    "file",
    "log",
    "test_result",
    "code_repository",
    "official_site",
    "web_page",
    "forum",
    "memory",
    "user",
    "tool_output",
    "unknown",
]

ClaimStatus = Literal[
    "extracted",
    "verified",
    "conflicted",
    "unverified",
]


DEFAULT_SOURCE_TRUST: dict[SourceType, float] = {
    "user": 1.00,
    "test_result": 0.95,
    "file": 0.90,
    "log": 0.88,
    "code_repository": 0.86,
    "official_site": 0.84,
    "documentation": 0.82,
    "pdf": 0.78,
    "book": 0.76,
    "article": 0.70,
    "web_page": 0.65,
    "video": 0.60,
    "podcast": 0.55,
    "memory": 0.55,
    "forum": 0.45,
    "tool_output": 0.45,
    "unknown": 0.10,
}


@dataclass(frozen=True)
class SourceRecord:
    """Catalog entry for one source."""

    id: str
    type: SourceType
    title: str
    locator: str
    author: str | None = None
    published_at: str | None = None
    trust_level: float = 0.5
    added_at: str = ""
    last_read_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "locator": self.locator,
            "author": self.author,
            "published_at": self.published_at,
            "trust_level": round(float(self.trust_level), 3),
            "added_at": self.added_at,
            "last_read_at": self.last_read_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceRecord":
        source_type = data.get("type", "unknown")
        if source_type not in DEFAULT_SOURCE_TRUST:
            source_type = "unknown"
        return cls(
            id=str(data.get("id") or new_id("src")),
            type=source_type,
            title=str(data.get("title") or ""),
            locator=str(data.get("locator") or ""),
            author=data.get("author") if isinstance(data.get("author"), str) else None,
            published_at=(
                data.get("published_at")
                if isinstance(data.get("published_at"), str)
                else None
            ),
            trust_level=_bounded_trust(data.get("trust_level", 0.5)),
            added_at=str(data.get("added_at") or ""),
            last_read_at=(
                data.get("last_read_at")
                if isinstance(data.get("last_read_at"), str)
                else None
            ),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ClaimRecord:
    """One extracted assertion tied to a source and location."""

    id: str
    source_id: str
    text: str
    locator: str = ""
    confidence: float = 0.5
    status: ClaimStatus = "extracted"
    extracted_at: str = ""
    support_source_ids: tuple[str, ...] = ()
    conflict_source_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "text": self.text,
            "locator": self.locator,
            "confidence": round(float(self.confidence), 3),
            "status": self.status,
            "extracted_at": self.extracted_at,
            "support_source_ids": list(self.support_source_ids),
            "conflict_source_ids": list(self.conflict_source_ids),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClaimRecord":
        status = data.get("status", "extracted")
        if status not in {"extracted", "verified", "conflicted", "unverified"}:
            status = "extracted"
        return cls(
            id=str(data.get("id") or new_id("claim")),
            source_id=str(data.get("source_id") or ""),
            text=str(data.get("text") or ""),
            locator=str(data.get("locator") or ""),
            confidence=_bounded_trust(data.get("confidence", 0.5)),
            status=status,
            extracted_at=str(data.get("extracted_at") or ""),
            support_source_ids=tuple(
                str(x) for x in (data.get("support_source_ids") or ()) if x
            ),
            conflict_source_ids=tuple(
                str(x) for x in (data.get("conflict_source_ids") or ()) if x
            ),
            metadata=dict(data.get("metadata") or {}),
        )


class SourceRegistry:
    """In-memory catalog of sources and claims."""

    def __init__(self) -> None:
        self._sources: dict[str, SourceRecord] = {}
        self._claims: dict[str, ClaimRecord] = {}

    @classmethod
    def from_provenance(
        cls,
        chain: ProvenanceChain,
        *,
        ranking: SourceRankingReport | None = None,
        claim_extractor: Any | None = None,
    ) -> "SourceRegistry":
        registry = cls()
        ranks_by_evidence = {
            rank.evidence_id: rank
            for rank in (ranking.ranks if ranking is not None else ())
        }
        for evidence in chain.evidences:
            rank = ranks_by_evidence.get(evidence.id)
            source = source_from_evidence(evidence, rank=rank)
            registry.add_source(source)
            if claim_extractor is not None:
                claims = claim_extractor.extract(evidence, source=source, rank=rank)
            elif evidence.claim:
                claims = [claim_from_evidence(evidence, source_id=source.id, rank=rank)]
            else:
                claims = []
            for claim in claims:
                registry.add_claim(claim)
        return registry

    @classmethod
    def from_records(
        cls,
        sources: Iterable[SourceRecord] = (),
        claims: Iterable[ClaimRecord] = (),
    ) -> "SourceRegistry":
        registry = cls()
        for source in sources:
            registry.add_source(source)
        for claim in claims:
            if claim.source_id in registry._sources:
                registry.add_claim(claim)
        return registry

    def add_source(self, source: SourceRecord) -> SourceRecord:
        self._sources[source.id] = source
        return source

    def register_source(
        self,
        *,
        type: SourceType,
        title: str,
        locator: str,
        author: str | None = None,
        published_at: str | None = None,
        trust_level: float | None = None,
        last_read_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SourceRecord:
        source = SourceRecord(
            id=new_id("src"),
            type=type,
            title=title,
            locator=locator,
            author=author,
            published_at=published_at,
            trust_level=_bounded_trust(
                DEFAULT_SOURCE_TRUST[type] if trust_level is None else trust_level
            ),
            added_at=_now_utc_iso(),
            last_read_at=last_read_at,
            metadata=dict(metadata or {}),
        )
        return self.add_source(source)

    def add_claim(self, claim: ClaimRecord) -> ClaimRecord:
        if claim.source_id not in self._sources:
            raise ValueError(f"claim source_id is not registered: {claim.source_id!r}")
        self._claims[claim.id] = claim
        return claim

    def register_claim(
        self,
        *,
        source_id: str,
        text: str,
        locator: str = "",
        confidence: float = 0.5,
        status: ClaimStatus = "extracted",
        support_source_ids: Iterable[str] = (),
        conflict_source_ids: Iterable[str] = (),
        metadata: dict[str, Any] | None = None,
    ) -> ClaimRecord:
        claim = ClaimRecord(
            id=new_id("claim"),
            source_id=source_id,
            text=text.strip(),
            locator=locator,
            confidence=_bounded_trust(confidence),
            status=status,
            extracted_at=_now_utc_iso(),
            support_source_ids=tuple(support_source_ids),
            conflict_source_ids=tuple(conflict_source_ids),
            metadata=dict(metadata or {}),
        )
        return self.add_claim(claim)

    @property
    def sources(self) -> tuple[SourceRecord, ...]:
        return tuple(self._sources.values())

    @property
    def claims(self) -> tuple[ClaimRecord, ...]:
        return tuple(self._claims.values())

    def get_source(self, source_id: str) -> SourceRecord | None:
        return self._sources.get(source_id)

    def claims_for_source(self, source_id: str) -> tuple[ClaimRecord, ...]:
        return tuple(claim for claim in self._claims.values() if claim.source_id == source_id)

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "source_count": len(self._sources),
            "claim_count": len(self._claims),
            "source_types": _counts(source.type for source in self._sources.values()),
            "claim_statuses": _counts(claim.status for claim in self._claims.values()),
            "sources": [source.to_dict() for source in self._sources.values()],
            "claims": [claim.to_dict() for claim in self._claims.values()],
        }


def source_from_evidence(evidence: Evidence, *, rank: SourceRank | None = None) -> SourceRecord:
    """Convert one Evidence record into a catalog SourceRecord."""

    source_type = source_type_from_evidence(evidence)
    locator = _strip_known_prefix(evidence.source_id)
    trust = rank.final_score if rank is not None else evidence.confidence
    metadata: dict[str, Any] = {
        "evidence_id": evidence.id,
        "evidence_kind": evidence.kind,
        "obtained_via": evidence.obtained_via,
        "content_hash": evidence.content_hash,
    }
    if rank is not None:
        metadata.update({
            "rank_tier": rank.tier,
            "freshness_status": rank.freshness_status,
            "support_level": rank.support_level,
            "rank_reasons": list(rank.reasons),
        })
    return SourceRecord(
        id=evidence.source_id,
        type=source_type,
        title=_title_for_source(evidence, source_type=source_type, locator=locator),
        locator=locator,
        trust_level=_bounded_trust(trust),
        added_at=evidence.fetched_at,
        last_read_at=evidence.fetched_at,
        metadata=metadata,
    )


def claim_from_evidence(
    evidence: Evidence,
    *,
    source_id: str | None = None,
    rank: SourceRank | None = None,
) -> ClaimRecord:
    """Create a first-pass claim extracted from Evidence metadata."""

    confidence = rank.final_score if rank is not None else evidence.confidence
    status: ClaimStatus = "extracted"
    if rank is not None and rank.support_level == "weak":
        status = "unverified"
    if rank is not None and rank.support_level == "insufficient_for_realtime":
        status = "unverified"
    return ClaimRecord(
        id=new_id("claim"),
        source_id=source_id or evidence.source_id,
        text=evidence.claim.strip(),
        locator=_strip_known_prefix(evidence.source_id),
        confidence=_bounded_trust(confidence),
        status=status,
        extracted_at=evidence.fetched_at,
        metadata={
            "evidence_id": evidence.id,
            "evidence_kind": evidence.kind,
        },
    )


def source_type_from_evidence(evidence: Evidence) -> SourceType:
    kind = evidence.kind
    if kind == "file":
        return "file"
    if kind == "web_page":
        return _web_source_type(evidence.source_id)
    if kind == "web_search_hit":
        return "web_page"
    if kind == "test_result":
        return "test_result"
    if kind == "log_event":
        return "log"
    if kind in {"shell_output", "tool_output", "diff_preview"}:
        return "tool_output"
    if kind == "memory":
        return "memory"
    if kind == "user_explicit":
        return "user"
    return "unknown"


def _web_source_type(source_id: str) -> SourceType:
    locator = _strip_known_prefix(source_id)
    domain = _domain(locator)
    if domain.startswith(("docs.", "developer.", "api.", "help.", "support.")):
        return "documentation"
    if domain.endswith(".gov") or domain.endswith(".edu"):
        return "official_site"
    if any(domain == item or domain.endswith("." + item) for item in ("reddit.com", "quora.com")):
        return "forum"
    return "web_page"


def _title_for_source(evidence: Evidence, *, source_type: SourceType, locator: str) -> str:
    if source_type == "file":
        return locator.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or locator
    if source_type in {"documentation", "official_site", "web_page", "forum"}:
        host = _domain(locator)
        return host or locator
    if source_type == "memory":
        return "Memory record"
    if source_type == "user":
        return "User directive"
    if source_type == "test_result":
        return "Test result"
    if source_type == "log":
        return "Agent log"
    if source_type == "tool_output":
        return evidence.obtained_via
    return evidence.source_id


def _strip_known_prefix(value: str) -> str:
    prefixes = (
        "file:",
        "web_page:",
        "web_search:",
        "test:",
        "log:",
        "shell:",
        "diff:",
        "memory:",
        "user:",
        "llm_claim:",
    )
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def _domain(locator: str) -> str:
    parsed = urlparse(locator)
    host = parsed.netloc or parsed.path.split("/", 1)[0]
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def _bounded_trust(value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(1.0, number))


def _counts(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
