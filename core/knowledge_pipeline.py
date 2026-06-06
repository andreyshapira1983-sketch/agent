"""Knowledge pipeline integration.

This layer connects the source stack:

    Evidence -> SourceRegistry -> extracted claims -> conflict resolver
    -> source catalog persistence -> optional long-term knowledge memory

It is deterministic and local. LLM-based claim extraction can replace the
extractor later, but the safety contract stays here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Literal

from core.evidence import Evidence, ProvenanceChain
from core.memory_policy import MemoryWriteDecision
from core.models import MemoryRecord
from core.secret_scanner import contains_secret
from core.source_ranker import SourceRank, SourceRankingReport
from core.source_registry import (
    ClaimRecord,
    SourceRecord,
    SourceRegistry,
    claim_from_evidence,
)
from core.source_registry_store import SourceRegistryStore
from core.truth_hype_filter import evaluate as evaluate_truth_hype


KnowledgeDecision = Literal["save", "reject"]


@dataclass(frozen=True)
class KnowledgeWriteDecision:
    decision: KnowledgeDecision
    reasons: tuple[str, ...] = ()
    policy_id: str = "knowledge-write-mvp"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reasons": list(self.reasons),
            "policy_id": self.policy_id,
        }


@dataclass(frozen=True)
class ConflictRecord:
    subject: str
    claim_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    values: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "claim_ids": list(self.claim_ids),
            "source_ids": list(self.source_ids),
            "values": list(self.values),
        }


@dataclass(frozen=True)
class ConflictReport:
    conflicts: tuple[ConflictRecord, ...] = ()

    @property
    def count(self) -> int:
        return len(self.conflicts)

    def conflicted_claim_ids(self) -> set[str]:
        out: set[str] = set()
        for conflict in self.conflicts:
            out.update(conflict.claim_ids)
        return out

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
        }


@dataclass
class KnowledgePipelineResult:
    registry: SourceRegistry
    conflicts: ConflictReport
    source_store: dict[str, int] = field(default_factory=dict)
    memory_saved: int = 0
    memory_rejected: int = 0
    memory_skipped: int = 0
    decisions: list[dict[str, Any]] = field(default_factory=list)

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "source_count": len(self.registry.sources),
            "claim_count": len(self.registry.claims),
            "conflicts": self.conflicts.to_log_payload(),
            "source_store": dict(self.source_store),
            "memory_saved": self.memory_saved,
            "memory_rejected": self.memory_rejected,
            "memory_skipped": self.memory_skipped,
            "decisions": list(self.decisions),
        }


RememberFn = Callable[
    [str, list[str], str, str, str],
    tuple[MemoryWriteDecision, MemoryRecord | None],
]


class ClaimExtractor:
    """Deterministic first-pass claim extractor from Evidence excerpts."""

    def __init__(
        self,
        *,
        max_claims_per_source: int = 5,
        min_chars: int = 18,
        max_chars: int = 320,
    ):
        self.max_claims_per_source = max_claims_per_source
        self.min_chars = min_chars
        self.max_chars = max_chars

    def extract(
        self,
        evidence: Evidence,
        *,
        source: SourceRecord,
        rank: SourceRank | None = None,
    ) -> list[ClaimRecord]:
        claims: list[ClaimRecord] = []
        if _is_meaningful_claim(evidence.claim):
            claims.append(claim_from_evidence(evidence, source_id=source.id, rank=rank))

        for sentence in _sentences(evidence.excerpt):
            if len(claims) >= self.max_claims_per_source:
                break
            if not self._accept_sentence(sentence):
                continue
            confidence = rank.final_score if rank is not None else evidence.confidence
            status = _status_from_rank(rank)
            claims.append(ClaimRecord(
                id=_claim_id(),
                source_id=source.id,
                text=sentence,
                locator=source.locator,
                confidence=_bounded(confidence),
                status=status,
                extracted_at=evidence.fetched_at,
                metadata={
                    "evidence_id": evidence.id,
                    "evidence_kind": evidence.kind,
                    "extraction": "sentence",
                },
            ))

        # Stable dedup inside one source.
        out: list[ClaimRecord] = []
        seen: set[str] = set()
        for claim in claims:
            key = " ".join(claim.text.casefold().split())
            if key in seen:
                continue
            seen.add(key)
            out.append(claim)
        return out

    def _accept_sentence(self, sentence: str) -> bool:
        text = sentence.strip()
        if len(text) < self.min_chars or len(text) > self.max_chars:
            return False
        if contains_secret(text)[0]:
            return False
        if text.count("{") + text.count("[") > 4:
            return False
        if not re.search(r"[A-Za-zА-Яа-я]", text):
            return False
        words = re.findall(r"[\w]+", text, flags=re.UNICODE)
        return len(words) >= 4


class ConflictResolver:
    """Detect obvious contradictory claims over the same subject."""

    def resolve(self, registry: SourceRegistry) -> tuple[SourceRegistry, ConflictReport]:
        grouped: dict[str, list[tuple[ClaimRecord, str]]] = {}
        for claim in registry.claims:
            parsed = _subject_value(claim.text)
            if parsed is None:
                continue
            subject, value = parsed
            grouped.setdefault(subject, []).append((claim, value))

        conflicts: list[ConflictRecord] = []
        conflicted_ids: set[str] = set()
        # claim_id -> the OTHER independent source_ids that agree with it.
        # A claim corroborated by >=2 independent sources (single agreed value,
        # no contradiction) is promoted to "verified" below — this is the
        # honest "усвоил и проверил" step: a fact is verified only when more
        # than one independent source attests it.
        corroborated: dict[str, tuple[str, ...]] = {}
        for subject, pairs in grouped.items():
            values = sorted({value for _claim, value in pairs})
            source_ids = sorted({claim.source_id for claim, _value in pairs})
            if len(source_ids) < 2:
                continue
            if len(values) >= 2:
                claim_ids = tuple(claim.id for claim, _value in pairs)
                conflicted_ids.update(claim_ids)
                conflicts.append(ConflictRecord(
                    subject=subject,
                    claim_ids=claim_ids,
                    source_ids=tuple(source_ids),
                    values=tuple(values),
                ))
            else:
                # One agreed value from >=2 independent sources -> corroboration.
                for claim, _value in pairs:
                    others = tuple(s for s in source_ids if s != claim.source_id)
                    if others:
                        corroborated[claim.id] = others

        if not conflicted_ids and not corroborated:
            return registry, ConflictReport()

        resolved = SourceRegistry()
        for source in registry.sources:
            resolved.add_source(source)
        for claim in registry.claims:
            if claim.id in conflicted_ids:
                conflict_sources = sorted({
                    conflict_source
                    for conflict in conflicts
                    if claim.id in conflict.claim_ids
                    for conflict_source in conflict.source_ids
                    if conflict_source != claim.source_id
                })
                resolved.add_claim(replace(
                    claim,
                    status="conflicted",
                    conflict_source_ids=tuple(conflict_sources),
                ))
            elif claim.id in corroborated and claim.status == "extracted":
                # Only a normally-extracted claim is promoted; a weak-source
                # "unverified" claim stays weak even if echoed, so two weak
                # sources cannot manufacture a "verified" fact.
                merged_support = tuple(sorted(
                    set(claim.support_source_ids) | set(corroborated[claim.id])
                ))
                resolved.add_claim(replace(
                    claim,
                    status="verified",
                    support_source_ids=merged_support,
                ))
            else:
                resolved.add_claim(claim)
        return resolved, ConflictReport(tuple(conflicts))


class KnowledgeWritePolicy:
    """Gate source-backed claims before they become long-term memory."""

    def __init__(
        self,
        *,
        min_claim_confidence: float = 0.65,
        min_source_trust: float = 0.55,
        max_chars: int = 900,
    ):
        self.min_claim_confidence = min_claim_confidence
        self.min_source_trust = min_source_trust
        self.max_chars = max_chars

    def decide(
        self,
        claim: ClaimRecord,
        *,
        source: SourceRecord | None,
    ) -> KnowledgeWriteDecision:
        reasons: list[str] = []
        text = (claim.text or "").strip()
        if source is None:
            return KnowledgeWriteDecision("reject", ("source not registered",))
        if not text:
            return KnowledgeWriteDecision("reject", ("empty claim",))
        if len(text) > self.max_chars:
            return KnowledgeWriteDecision("reject", (f"claim too long (>{self.max_chars})",))
        if contains_secret(text)[0]:
            return KnowledgeWriteDecision("reject", ("claim contains secret material",))
        # Truth/Hype filter (first LEARNING antibody): promotional content with
        # no checkable substance is "шумиха", not knowledge — never absorb it.
        _th = evaluate_truth_hype(text)
        if _th.is_hype:
            return KnowledgeWriteDecision(
                "reject",
                (f"claim is promotional hype (no checkable substance): "
                 f"{'; '.join(_th.reasons[:2])}",),
            )
        if claim.status in {"unverified", "conflicted"}:
            return KnowledgeWriteDecision("reject", (f"claim status is {claim.status}",))
        if claim.confidence < self.min_claim_confidence:
            return KnowledgeWriteDecision(
                "reject",
                (f"claim confidence {claim.confidence:.2f} < {self.min_claim_confidence:.2f}",),
            )
        if source.trust_level < self.min_source_trust:
            return KnowledgeWriteDecision(
                "reject",
                (f"source trust {source.trust_level:.2f} < {self.min_source_trust:.2f}",),
            )
        if source.type in {"forum", "unknown", "tool_output"}:
            return KnowledgeWriteDecision("reject", (f"source type {source.type} is too weak",))
        reasons.append(f"claim confidence={claim.confidence:.2f}")
        reasons.append(f"source trust={source.trust_level:.2f}")
        reasons.append(f"source type={source.type}")
        return KnowledgeWriteDecision("save", tuple(reasons))

    def memory_content(self, claim: ClaimRecord, source: SourceRecord) -> str:
        return (
            f"{claim.text}\n"
            f"Source: {source.type}:{source.locator}\n"
            f"Confidence: {claim.confidence:.2f}"
        )

    def memory_tags(self, claim: ClaimRecord, source: SourceRecord) -> list[str]:
        return ["fact", "knowledge", "source-backed", source.type]


class KnowledgePipeline:
    """End-to-end knowledge integration for one agent cycle."""

    def __init__(
        self,
        *,
        claim_extractor: ClaimExtractor | None = None,
        conflict_resolver: ConflictResolver | None = None,
        write_policy: KnowledgeWritePolicy | None = None,
    ):
        self.claim_extractor = claim_extractor or ClaimExtractor()
        self.conflict_resolver = conflict_resolver or ConflictResolver()
        self.write_policy = write_policy or KnowledgeWritePolicy()

    def build_registry(
        self,
        chain: ProvenanceChain,
        *,
        ranking: SourceRankingReport | None = None,
    ) -> tuple[SourceRegistry, ConflictReport]:
        registry = SourceRegistry.from_provenance(
            chain,
            ranking=ranking,
            claim_extractor=self.claim_extractor,
        )
        return self.conflict_resolver.resolve(registry)

    def run(
        self,
        chain: ProvenanceChain,
        *,
        ranking: SourceRankingReport | None = None,
        source_store: SourceRegistryStore | None = None,
        remember: RememberFn | None = None,
        auto_write_memory: bool = False,
    ) -> KnowledgePipelineResult:
        registry, conflicts = self.build_registry(chain, ranking=ranking)
        result = KnowledgePipelineResult(registry=registry, conflicts=conflicts)

        if source_store is not None:
            result.source_store = source_store.save_registry(registry)

        if not auto_write_memory or remember is None:
            result.memory_skipped = len(registry.claims)
            return result

        for claim in registry.claims:
            source = registry.get_source(claim.source_id)
            decision = self.write_policy.decide(claim, source=source)
            row: dict[str, Any] = {
                "claim_id": claim.id,
                "source_id": claim.source_id,
                "knowledge_decision": decision.to_dict(),
            }
            if decision.decision == "reject" or source is None:
                result.memory_rejected += 1
                result.decisions.append(row)
                continue
            memory_decision, record = remember(
                self.write_policy.memory_content(claim, source),
                self.write_policy.memory_tags(claim, source),
                "agent-auto",
                "semantic",
                "self",
            )
            row["memory_decision"] = {
                "decision": memory_decision.decision,
                "reasons": list(memory_decision.reasons),
                "policy_id": memory_decision.policy_id,
                "record_id": record.id if record is not None else None,
            }
            if memory_decision.decision == "save":
                result.memory_saved += 1
            else:
                result.memory_rejected += 1
            result.decisions.append(row)
        return result


def _sentences(text: str) -> list[str]:
    pieces: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"(?<=[.!?])\s+|[•;]\s+", line)
        pieces.extend(part.strip(" -\t") for part in parts if part.strip(" -\t"))
    return pieces


def _is_meaningful_claim(text: str) -> bool:
    lowered = (text or "").casefold()
    generic = (
        "contents of workspace file",
        "fetched page",
        "search for",
        "tool ",
        "read ",
        "ran `",
    )
    return bool(text and not any(lowered.startswith(prefix) for prefix in generic))


def _status_from_rank(rank: SourceRank | None) -> str:
    if rank is None:
        return "extracted"
    if rank.support_level in {"weak", "insufficient_for_realtime"}:
        return "unverified"
    return "extracted"


def _subject_value(text: str) -> tuple[str, str] | None:
    compact = " ".join((text or "").strip().rstrip(".").split())
    if len(compact) < 8:
        return None
    patterns = (
        r"^(.{3,80}?)\s+(?:is|are|=|:)\s+(.{1,120})$",
        r"^(.{3,80}?)\s+(?:это|является|=|:)\s+(.{1,120})$",
    )
    for pattern in patterns:
        match = re.match(pattern, compact, flags=re.IGNORECASE)
        if not match:
            continue
        subject = _normalise_subject(match.group(1))
        value = _normalise_value(match.group(2))
        if subject in _GENERIC_CONFLICT_SUBJECTS:
            continue
        if subject and value:
            return subject, value
    return None


def _normalise_subject(text: str) -> str:
    text = re.sub(r"^(the|a|an|этот|эта|это)\s+", "", text.strip().casefold())
    return re.sub(r"[^0-9a-zа-яё _-]+", "", text).strip()


def _normalise_value(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().casefold())


def _bounded(value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(1.0, number))


def _claim_id() -> str:
    from core.ids import new_id

    return new_id("claim")


_GENERIC_CONFLICT_SUBJECTS = {
    "it",
    "this",
    "that",
    "these",
    "those",
    "here",
    "there",
    "он",
    "она",
    "оно",
    "это",
    "этот",
    "эта",
    "эти",
    "то",
}
