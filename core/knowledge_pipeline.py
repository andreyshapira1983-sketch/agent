"""Knowledge pipeline integration."""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from core.evidence import Evidence, ProvenanceChain, _is_own_state_path, _path_inside_agent
from core.evidence_budget import carries_framework_notice
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
from core.unit_score import clamp_unit as _bounded

KnowledgeDecision = Literal["save", "reject"]


#: Token that core/redaction.py puts in place of a cut secret ([REDACTED:<kind>], kind lowercase-hyphenated); its presence means
#: the text is a leftover of secret material. The word "redacted" in prose does not match.
_REDACTION_MARKER_RE = re.compile(r"\[REDACTED:[a-z0-9-]+\]")


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

        # Hidden text never becomes a claim: structural, independent of the attacker's wording (docs/CODE_NOTES.md, "Concealment, not vocabulary").
        from core.injection_guard import (
            strip_concealed,
            strip_suspicious_annotation,
        )

        # Detect guard flags BEFORE stripping; claims minted from a flagged excerpt are quarantined as `suspect` (stored, refused by write policy,
        # never upgraded by corroboration). Taint is per sentence, not per document: document-level taint would quarantine legitimate sources.
        suspect_spans = _suspicious_spans(evidence.excerpt)

        # The guard's wrapper annotation is not source content: strip it before sentence splitting or the warning becomes a "fact".
        for sentence in _statements(
            strip_suspicious_annotation(strip_concealed(evidence.excerpt))
        ):
            if len(claims) >= self.max_claims_per_source:
                break
            if not self._accept_sentence(sentence):
                continue
            confidence = rank.final_score if rank is not None else evidence.confidence
            # Compared with hidden text removed and whitespace collapsed on both sides, else a hidden comment or zero-width char inside the sentence clears the taint.
            flat = _flat(sentence)
            tainted = any(
                (fs := _flat(span)) and (fs in flat or flat in fs)
                for span in suspect_spans
            )
            status = "suspect" if tainted else _status_from_rank(rank)
            claims.append(ClaimRecord(
                id=_claim_id(source.locator, sentence),
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
        # A sentence carrying a trimmer's notice was never fully written by the source: refused, not cleaned. The grammar lives in core/evidence_budget.py, keyed on shape.
        if carries_framework_notice(text):
            return False
        if text.count("{") + text.count("[") > 4:
            return False
        if not re.search(r"[A-Za-zА-Яа-я]", text):
            return False
        if _is_broken_encoding(text) or _looks_like_code_fragment(text):
            # Mojibake and raw source-code / CLI / mid-sentence fragments are not
            # facts — they are file chunks that flooded memory as distractors.
            return False
        if _is_truncated_text(text):
            # Same family: a truncated sentence is a file chunk, not a fact.
            return False
        words = re.findall(r"[\w]+", text, flags=re.UNICODE)
        return len(words) >= 4


def claim_provenance_tag(claim_id: str) -> str:
    """Tag linking a memory record back to the claim it came from."""
    return f"claim:{claim_id}"


def quarantine_conflicted_records(
    records: list[MemoryRecord], *, conflicted_claim_ids: set[str]
) -> tuple[list[MemoryRecord], dict]:
    """Tag records whose originating claim is now contradicted; idempotent, and only the operator removes the tag."""
    report = {"quarantined": 0, "already_quarantined": 0, "unlinked": 0, "unaffected": 0}
    if not conflicted_claim_ids:
        report["unaffected"] = len(records)
        return list(records), report

    wanted = {claim_provenance_tag(cid) for cid in conflicted_claim_ids}
    out: list[MemoryRecord] = []
    for record in records:
        tags = list(record.tags or [])
        if not any(t.startswith("claim:") for t in tags):
            report["unlinked"] += 1
            out.append(record)
            continue
        if not (set(tags) & wanted):
            report["unaffected"] += 1
            out.append(record)
            continue
        if "conflicted" in tags:
            report["already_quarantined"] += 1
            out.append(record)
            continue
        report["quarantined"] += 1
        out.append(record.model_copy(update={"tags": [*tags, "conflicted"]}))
    return out, report


# Source types that do not ASSERT anything (memory, log/tool_output/test_result, code_repository, code files): "X is Y" read out of them is not a
# proposition and two of them differing is no contradiction. Everything else stays in scope, including low-trust `forum`, which `trust_level` handles.
_NON_ASSERTING_SOURCE_TYPES = frozenset({
    "memory", "log", "tool_output", "test_result", "code_repository",
    # dialogue: verbatim conversation turns; two turns on one subject are a conversation move, not a dispute.
    "dialogue",
})

# Address scheme of conversation turns; checked apart from the type because rows written before the "dialogue" type exist on disk as "unknown".
_DIALOGUE_LOCATOR_PREFIX = "session_dialogue:"


def _is_dialogue_locator(locator: str) -> bool:
    return (locator or "").strip().casefold().startswith(_DIALOGUE_LOCATOR_PREFIX)

# Extensions whose contents are programs, not assertions; prose files (.md, .txt) stay in scope.
_CODE_SUFFIXES = frozenset({
    ".py", ".pyi", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".sh", ".ps1", ".bat", ".sql", ".toml",
    ".ini", ".cfg", ".yaml", ".yml", ".json",
})


#: Address decorations that do not change its nature: line range, line number, fragment label (file_read windows give core/x.py:284-292).
_LOCATOR_DECORATION_RE = re.compile(r":\d+(?:-\d+)?$|#L\d+(?:-L?\d+)?$")


def _document_key(locator: str) -> str:
    """Which document a locator names, however written; one document is one witness with one claim id; a web address keeps its port."""
    raw = (locator or "").strip()
    if "://" in raw:
        # Web: the path is case-sensitive and the port names the server; only a line anchor is decoration.
        return re.sub(r"#L\d+(?:-L?\d+)?$", "", raw, flags=re.IGNORECASE)
    text = raw.casefold().replace("\\", "/").removeprefix("file:")
    # A line window is stripped only from a FILE path, never from an identifier like pmid:123 (two papers are two witnesses).
    if re.search(r"\.\w+(?::\d+(?:-\d+)?|#l\d+(?:-l?\d+)?)$", text):
        text = _bare_locator(text)
    inside = _path_inside_agent(text)
    return inside if inside and inside != "." else text


def _bare_locator(locator: str) -> str:
    """Locator without decorations: core/x.py:284-292 -> core/x.py."""
    text = (locator or "").strip().casefold()
    while True:
        stripped = _LOCATOR_DECORATION_RE.sub("", text)
        if stripped == text:
            return text
        text = stripped


def _is_code_locator(locator: str) -> bool:
    """True when the locator points at a program, not prose; normalized before the check so a line-range suffix does not defeat it."""
    return any(_bare_locator(locator).endswith(suffix) for suffix in _CODE_SUFFIXES)


#: Places holding what the agent wrote itself (edit drafts, journals, state, traces, script-built docs); journal formats record one moment, not a claim.
_OWN_ARTIFACT_SUFFIXES = (".jsonl", ".log")


def _is_own_artifact(locator: str) -> bool:
    """True when the locator points at a file the agent wrote itself (edit drafts, outbox, script-generated docs).

    Decided by provenance, not content: own text re-told looks like independent knowledge (memory poisoning in its quiet form).
    Human-written docs and books stay in scope.
    """
    bare = _bare_locator(locator).replace("\\", "/")
    if "://" in bare:
        # A web address is never the agent's own file, whatever its path says.
        return False
    return _is_own_state_path(bare) or bare.endswith(_OWN_ARTIFACT_SUFFIXES)


_WEAK_SOURCE_TYPES = frozenset({"forum", "unknown"})
_REJECTED_CLAIM_STATUSES = frozenset({"unverified", "conflicted"})


class ConflictResolver:
    """Detect obvious contradictory claims over the same subject."""

    def __init__(self, *, policy: KnowledgeWritePolicy | None = None):
        self.policy = policy or KnowledgeWritePolicy()  # its weakness rule judges witnesses

    def resolve(self, registry: SourceRegistry) -> tuple[SourceRegistry, ConflictReport]:
        grouped: dict[str, list[tuple[ClaimRecord, str]]] = {}
        sources_by_id = {source.id: source for source in registry.sources}
        for claim in registry.claims:
            # Contradiction is meaningful only between sources that assert things; code, own memory, logs and tool output do not.
            source = sources_by_id.get(claim.source_id)
            if source is None or source.type in _NON_ASSERTING_SOURCE_TYPES:
                continue
            if _is_code_locator(source.locator):
                continue
            if _is_dialogue_locator(source.locator):
                continue
            # A scanner-flagged claim neither creates a conflict nor is relabelled: a planted sentence must not quarantine a clean claim, and its taint must survive.
            if claim.status == "suspect":
                continue
            parsed = _subject_value(claim.text)
            if parsed is None:
                continue
            subject, value = parsed
            grouped.setdefault(subject, []).append((claim, value))

        conflicts: list[ConflictRecord] = []
        conflicted_ids: set[str] = set()
        # claim_id -> the OTHER independent source_ids that agree with it; >=2 independent sources with one agreed value promote a claim to "verified".
        corroborated: dict[str, tuple[str, ...]] = {}

        def _origin(claim: ClaimRecord) -> str:
            # Independence is by DOCUMENT, not source id: two windows of one file are one witness.
            src = sources_by_id.get(claim.source_id)
            return _document_key(src.locator) if src is not None else claim.source_id

        def _witness(claim: ClaimRecord) -> bool:
            # The agent's own files (data/, logs/, drafts) and sources too weak to save are not a second witness.
            src = sources_by_id.get(claim.source_id)
            if src is None or claim.status == "suspect" or claim.status in _REJECTED_CLAIM_STATUSES:
                return False
            return not _is_own_artifact(src.locator) and self.policy.weak_source_reason(src) is None

        for subject, pairs in grouped.items():
            values = _distinct_values(value for _claim, value in pairs)
            source_ids = sorted({claim.source_id for claim, _value in pairs})
            if len({_origin(claim) for claim, _value in pairs}) < 2:
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
                # One agreed value from >=2 independent sources is corroboration; a witness must be another document, and a suspect claim corroborates nothing.
                for claim, _value in pairs:
                    own_src = sources_by_id.get(claim.source_id)
                    if own_src is None or self.policy.weak_source_reason(own_src) is not None:
                        continue  # a source too weak to save is not promoted either
                    mine = _origin(claim)
                    others = tuple(sorted({
                        other.source_id for other, _v in pairs
                        if _origin(other) != mine and _witness(other)
                    }))
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
                # Only a normally-extracted claim is promoted; a weak-source "unverified" claim stays weak even if echoed.
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


_INTERNET_SOURCE_PREFIXES = ("web_page:", "web_search:", "web:", "search:", "rss:", "semantic_scholar:")


def _is_internet_source(source_id: str, source: SourceRecord | None) -> bool:
    """Whether the claim came from the network, by evidence address or source locator."""
    sid = (source_id or "").lower()
    locator = (getattr(source, "locator", "") or "").lower()
    return sid.startswith(_INTERNET_SOURCE_PREFIXES) or locator.startswith(("http://", "https://"))


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

    def weak_source_reason(self, source: SourceRecord) -> str | None:
        """Why the source is too weak to back a fact (to save it or to witness it), else None."""
        if source.trust_level < self.min_source_trust:
            return f"source trust {source.trust_level:.2f} < {self.min_source_trust:.2f}"
        if source.type in _WEAK_SOURCE_TYPES:
            return f"source type {source.type} is too weak"
        return None

    def decide(  # noqa: PLR0911 — flat: depth 1, all 15 returns are guard clauses
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
        # Redaction leftover: text arrives already cleaned, so TELEGRAM=[REDACTED:...] fits no key pattern; the marker itself proves the
        # material was secret (the source verdict class=secret does not reach this policy).
        if _REDACTION_MARKER_RE.search(text):
            return KnowledgeWriteDecision(
                "reject", ("claim is redacted secret residue",)
            )
        # Truth/Hype filter: promotional content with no checkable substance is not knowledge.
        _th = evaluate_truth_hype(text)
        if _th.is_hype:
            return KnowledgeWriteDecision(
                "reject",
                ((f"claim is promotional hype (no checkable substance): "
                 f"{'; '.join(_th.reasons[:2])}"),),
            )
        # A label WE wrote for a tool result (Evidence.claim is framework-authored; the source's words live in excerpt) is not an assertion
        # about the world; decided on provenance, not vocabulary. getattr, not attribute access: decide is fed duck-typed claims, and a
        # missing optional field must skip this check, not crash the write path.
        if (getattr(claim, "metadata", None) or {}).get("extraction") == "evidence_claim":
            return KnowledgeWriteDecision(
                "reject",
                (
                    ("claim is our own label for a tool result, not an "
                     "assertion by the source (source words live in the "
                     "excerpt)"),
                ),
            )
        if claim.status == "suspect":
            # Injection guard flagged the source content: kept in the registry for audit, never durable knowledge.
            return KnowledgeWriteDecision(
                "reject",
                (("claim was extracted from scanner-flagged content "
                  "(injection guard: suspicious) — quarantined, not knowledge"),),
            )
        if claim.status in _REJECTED_CLAIM_STATUSES:
            return KnowledgeWriteDecision("reject", (f"claim status is {claim.status}",))
        if claim.confidence < self.min_claim_confidence:
            return KnowledgeWriteDecision(
                "reject",
                (f"claim confidence {claim.confidence:.2f} < {self.min_claim_confidence:.2f}",),
            )
        weak = self.weak_source_reason(source)
        if weak:
            return KnowledgeWriteDecision("reject", (weak,))
        if source.type in _NON_ASSERTING_SOURCE_TYPES:
            # Same doctrine as the conflict resolver: a log line, test verdict, tool dump or own memory is an observation of one moment;
            # trust cannot rescue it, so reject by assertion class before the trust ladder.
            return KnowledgeWriteDecision(
                "reject",
                ((f"source type {source.type} does not assert facts "
                 f"(observation of one moment, not a standing claim)"),),
            )
        if source.type == "file" and _is_code_locator(source.locator):
            # Programs do not assert (x = y is an assignment, assert is a test statement); prose files (.md, .txt) stay in scope.
            return KnowledgeWriteDecision(
                "reject",
                ((f"source is a code file ({source.locator}): programs do not "
                 f"assert facts"),),
            )
        if source.type == "file" and _is_own_artifact(source.locator):
            return KnowledgeWriteDecision(
                "reject",
                ((f"source is the agent's own artifact ({source.locator}): its "
                  f"drafts, logs and generated files are not evidence about the "
                  f"world"),),
            )
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
        # claim:<id> is provenance: it lets a record be found again if its claim is later contradicted.
        return [
            "fact", "knowledge", "source-backed", source.type,
            claim_provenance_tag(claim.id),
        ]


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
        self.write_policy = write_policy or KnowledgeWritePolicy()
        self.conflict_resolver = conflict_resolver or ConflictResolver(policy=self.write_policy)

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
        require_verified: bool = False,
        admit_internet: bool = True,
    ) -> KnowledgePipelineResult:
        registry, conflicts = self.build_registry(chain, ranking=ranking)
        result = KnowledgePipelineResult(registry=registry, conflicts=conflicts)

        if source_store is not None:
            result.source_store = source_store.save_registry(registry)

        if not auto_write_memory or remember is None:
            # A decision row per claim here too, naming the rule that refused: a bare counter, or the same policy_id on both branches, hides the reason.
            reason, policy_id = (
                ("auto_write_memory is off", "auto_write_memory")
                if not auto_write_memory
                else ("no memory writer is wired", "memory_writer_missing")
            )
            for claim in registry.claims:
                result.memory_skipped += 1
                result.decisions.append({
                    "claim_id": claim.id,
                    "source_id": claim.source_id,
                    "knowledge_decision": {
                        "decision": "skip",
                        "reasons": [reason],
                        "policy_id": policy_id,
                    },
                })
            return result

        for claim in registry.claims:
            if not admit_internet and _is_internet_source(claim.source_id, registry.get_source(claim.source_id)):
                # Two worlds: the local library is the source of truth, the internet must still be qualified. A network claim stays in the source
                # registry for audit; only deliberate ingestion (ingestion.py) makes it a memory fact.
                result.memory_skipped += 1
                result.decisions.append({
                    "claim_id": claim.id,
                    "source_id": claim.source_id,
                    "knowledge_decision": {
                        "decision": "skip",
                        "reasons": [("internet source: kept in the source registry to be qualified, "
                                     "not promoted to durable memory by an ordinary turn")],
                        "policy_id": "two_worlds",
                    },
                })
                continue
            if require_verified and claim.status != "verified":
                # SKIPPED, not rejected: the claim was never corroborated (verified is set in build_registry only with a second source and normal
                # extraction). For the UNATTENDED path: a human running :ingest can judge, an overnight campaign cannot.
                # Still a decision row: a claim vanishing without one leaves a counter and no reason.
                result.memory_skipped += 1
                result.decisions.append({
                    "claim_id": claim.id,
                    "source_id": claim.source_id,
                    "knowledge_decision": {
                        "decision": "skip",
                        "reasons": [
                            (f"claim status is {claim.status!r}, not "
                             "'verified': an unattended run writes only "
                             "what a second source corroborated"),
                        ],
                        "policy_id": "require_verified",
                    },
                })
                continue
            if claim_source_is_untrusted(claim.text):
                # Barred, not merely rejected: this text tried to give orders.
                result.memory_rejected += 1
                result.decisions.append({
                    "claim_id": claim.id,
                    "source_id": claim.source_id,
                    "knowledge_decision": {
                        "decision": "reject",
                        "reasons": [
                            ("injection guard flagged the claim text; "
                             "an instruction is not a fact"),
                        ],
                        "policy_id": "injection_guard",
                    },
                })
                continue
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



def claim_source_is_untrusted(text: str) -> bool:
    """True when the injection guard refuses this text."""
    from core.injection_guard import scan_for_injection

    return scan_for_injection(text or "").is_blocked


_WINDOW_LINE_NO = re.compile(r"^\s*\d+:\s?")
_LIST_ITEM = re.compile(r"^(?:[-*•]|\d+[.)])\s+")
_TERMINAL = re.compile(r"[.!?][\"'»”)\]]*$")


def _statements(text: str) -> list[str]:
    """Complete statements of the source, candidates for facts.

    Window header and line numbers are removed and wrapped lines re-joined (a continuation starts lowercase); only a finished
    sentence or list item counts, not titles, headings or fragments.
    """
    lines = (text or "").splitlines()
    if lines and _READ_WINDOW_HEADER.match(lines[0].strip()):
        lines = [_WINDOW_LINE_NO.sub("", ln, count=1) for ln in lines[1:]]
    units: list[tuple[str, bool]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            units.append(("", False))
            continue
        item = bool(_LIST_ITEM.match(line))
        structural = item or line.startswith(("#", "|", ">"))
        prev, prev_item = units[-1] if units else ("", False)
        if (prev and not structural and not re.search(r"[.!?:]$", prev)
                and re.match(r"^[a-zа-яё(]", line)):
            units[-1] = (f"{prev}{line}" if prev.endswith("-") else f"{prev} {line}", prev_item)
        else:
            units.append((line, item))
    out: list[str] = []
    for unit, item in units:
        for part in re.split(r"(?<=[.!?])\s+|[•;]\s+", unit):
            part = part.strip(" -\t")
            if part and (item or _TERMINAL.search(part)):
                out.append(part)
    return out


def _sentences(text: str) -> list[str]:
    pieces: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"(?<=[.!?])\s+|[•;]\s+", line)
        pieces.extend(part.strip(" -\t") for part in parts if part.strip(" -\t"))
    return pieces


def _is_broken_encoding(text: str) -> bool:
    """True when the text carries mojibake markers (the Unicode replacement character means a wrong codec upstream); never a fact."""
    if "�" in text:
        return True
    # A high density of replacement chars or lone control bytes also indicates
    # corruption even if a few survived.
    bad = sum(1 for ch in text if ch == "�" or (ord(ch) < 0x20 and ch not in "\t\n\r"))
    return bad > 0


# Prefixes that mark a line as source code, a shell/REPL transcript, or a
# doc/CLI example rather than a natural-language fact.
_CODE_LINE_PREFIXES = (
    "import ", "from ", "def ", "class ", "return ", "raise ", "yield ",
    "async def ", "await ", "@", "#", "//", "/*", "*", '"""', "'''",
    ">>>", ">>> ", "> ", "$ ", "python ", "pip ", "uvicorn ", "pytest ",
    "git ", "cd ", "curl ", "npm ", "--", "```",
    # Statement keywords prose never opens with. Deliberately NOT listed: "if ", "for ", "while ", "with ", "except ", "global ", "pass",
    # "del ", "finally:", "lambda " -- real prose facts open with them.
    "assert ", "elif ", "else:", "try:",
    "self.",
)

# Dangling trailing tokens that reveal a mid-sentence chunk cut, not a whole fact.
_DANGLING_TAIL = {
    "and", "or", "but", "the", "a", "an", "of", "to", "for", "with", "in",
    "on", "at", "as", "by", "from", "that", "which", "и", "или", "в", "на",
    "с", "по", "что", "для",
}


#: Header that file_read puts before a file chunk.
_READ_WINDOW_HEADER = re.compile(r"^\[[^\]]*\blines?\s+\d+\s*-\s*\d+\s+of\s+\d+\]$", re.IGNORECASE)
#: Marks on which a finished thought does not end.
_CUT_PUNCTUATION = (",", ":", ";", "—", "–", "(", "⟹", "→", "⇒")


def _looks_like_code_fragment(text: str) -> bool:
    """True when the line is source code / CLI / a mid-sentence file chunk; high-precision heuristics that keep raw fragments out of memory."""
    stripped = text.strip()
    low = stripped.casefold()
    if any(low.startswith(p.casefold()) for p in _CODE_LINE_PREFIXES):
        return True
    # The file_read window header ("[data.csv lines 1-20 of 61]") must not be saved as a fact.
    if _READ_WINDOW_HEADER.match(stripped):
        return True
    # Cut at a mark after which the thought must continue:
    # «…, library/txt/Beck.txt —», «Карта строк txt:».
    if stripped.endswith(_CUT_PUNCTUATION):
        return True
    # Assignment / call-heavy code without sentence structure.
    if ("=" in stripped or "()" in stripped) and stripped.endswith((":", ")", "}", ";")):
        return True
    if stripped.count("(") + stripped.count(")") >= 4:
        return True
    # A chunk cut mid-sentence: ends on a conjunction/preposition/article and
    # has no terminal punctuation.
    last = re.findall(r"[\w']+", low)
    return bool(last and last[-1] in _DANGLING_TAIL and not stripped.endswith((".", "!", "?")))


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


def _flat(text: str) -> str:
    """Hidden content removed, whitespace collapsed — the form taint is compared in."""
    from core.injection_guard import strip_concealed

    return " ".join(strip_concealed(text or "").split())


def _suspicious_spans(excerpt: str) -> list[str]:
    """Sentences the injection guard pointed at (by finding offset), wrapper-stripped; empty when clean or never wrapped."""
    from core.injection_guard import (
        carries_suspicious_annotation,
        scan_for_injection,
        strip_suspicious_annotation,
    )

    if not carries_suspicious_annotation(excerpt):
        return []
    body = strip_suspicious_annotation(excerpt)
    result = scan_for_injection(body)
    offsets = [int(getattr(f, "offset", -1)) for f in result.findings]
    offsets = [o for o in offsets if o >= 0]
    if not offsets:
        # Flagged on our own wrapper but the body scans clean now: treat the WHOLE body as suspect; losing the reason for a flag must not clear it.
        return _sentences(body)
    spans: list[str] = []
    cursor = 0
    for sentence in _sentences(body):
        start = body.find(sentence, cursor)
        if start < 0:
            continue
        cursor = start + len(sentence)
        if any(start <= o < cursor for o in offsets):
            spans.append(sentence)
    return spans or _sentences(body)


def _status_from_rank(rank: SourceRank | None) -> str:
    if rank is None:
        return "extracted"
    if rank.support_level in {"weak", "insufficient_for_realtime"}:
        return "unverified"
    return "extracted"


def _is_truncated_text(text: str) -> bool:
    """A truncated fragment asserts nothing whole; it must not read as a different value of the same subject."""
    tail = (text or "").rstrip()
    return "[truncated]" in tail or tail.endswith(("...", "…"))


#: A question is not a statement: "X is Y" from a question, or an interrogative word in the subject, is no claim about the world (a conflict quarantines its originating record).
_INTERROGATIVE_SUBJECT_RE = re.compile(
    r"(?:^|\s)(?:what|who|which|where|when|why|how|что|кто|какой|какая|какие|где|когда|почему|как)$",
    re.IGNORECASE)


def _subject_value(text: str) -> tuple[str, str] | None:
    if _is_truncated_text(text):
        return None
    compact = " ".join((text or "").strip().rstrip(".").split())
    if len(compact) < 8 or compact.endswith("?"):
        return None
    patterns = (
        r"^(.{3,80}?)\s+(?:is|are|=|:)\s+(.{1,120})$",
        r"^(.{3,80}?)\s+(?:это|является|=|:)\s+(.{1,120})$",
    )
    for pattern in patterns:
        match = re.match(pattern, compact, flags=re.IGNORECASE)
        if not match:
            continue
        raw_subject = match.group(1).strip()
        # A deictic noun phrase ("this document") names a different referent in every source; grouping it across sources manufactures conflicts.
        if _DEICTIC_SUBJECT_RE.match(raw_subject) or _INTERROGATIVE_SUBJECT_RE.search(raw_subject):
            continue
        subject = _normalise_subject(raw_subject)
        value = _normalise_value(match.group(2))
        if subject in _GENERIC_CONFLICT_SUBJECTS:
            continue
        if subject and value:
            return subject, value
    return None


#: Deictic determiners open a context-bound subject; articles (the/a/an) do
#: not and stay strippable in `_normalise_subject`.
_DEICTIC_SUBJECT_RE = re.compile(
    r"^(this|that|these|those|этот|эта|это|эти|тот|та|то|те|данный|данная|данное|данные)\b",
    re.IGNORECASE,
)


def _normalise_subject(text: str) -> str:
    text = re.sub(r"^(the|a|an)\s+", "", text.strip().casefold())
    return re.sub(r"[^0-9a-zа-яё _-]+", "", text).strip()


def _normalise_value(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().casefold())


#: Units that change the fact: "$5" is not "€5", "5%" is not "5"; they stay tokens of their own.
_VALUE_UNIT_CHARS = "$€£¥₽₪%"

#: A tail word that narrows the shorter value: "all regions" vs "all regions except the EU" are two facts.
_VALUE_QUALIFIERS = frozenset({
    "except", "excluding", "excl", "only", "until", "till", "unless", "but",
    "however", "before", "after", "without", "not", "no", "apart", "save",
    "кроме", "исключая", "исключением", "только", "лишь", "до", "но", "однако",
    "если", "без", "пока", "не", "после",
})

#: A tail word that changes the measure (unit, currency, scale, period): "up by 5" vs "up by 5%" are two facts.
_VALUE_MEASURE_WORDS = frozenset({
    "percent", "per", "pct", "bps", "points", "point",
    "dollar", "dollars", "usd", "eur", "euro", "euros", "gbp", "pound", "pounds",
    "yen", "jpy", "rub", "ruble", "rubles", "rouble", "roubles", "ils", "shekel", "shekels",
    "cent", "cents",
    "k", "m", "bn", "b", "mn", "thousand", "thousands", "million", "millions",
    "billion", "billions", "trillion", "trillions",
    "annually", "annual", "yearly", "monthly", "weekly", "daily", "hourly",
    "year", "years", "month", "months", "week", "weeks", "day", "days", "hour", "hours",
    "процент", "процента", "процентов", "руб", "рубль", "рубля", "рублей",
    "долларов", "доллар", "доллара", "евро", "шекель", "шекеля", "шекелей",
    "тыс", "млн", "млрд", "трлн",
    "ежегодно", "ежемесячно", "еженедельно", "ежедневно",
    "год", "года", "лет", "месяц", "месяца", "месяцев", "неделю", "недели", "день", "дня", "дней",
    "час", "часа", "часов",
})
_VALUE_MEASURE_STEMS = ("тысяч", "миллион", "миллиард", "триллион")


def _is_measure_token(token: str) -> bool:
    return (
        token in _VALUE_MEASURE_WORDS
        or (len(token) == 1 and token in _VALUE_UNIT_CHARS)
        or token.startswith(_VALUE_MEASURE_STEMS)
    )


def _value_tokens(value: str) -> list[str]:
    """Word tokens for equivalence comparison, negation canonicalised,
    currency and percent signs kept as tokens."""
    units = re.escape(_VALUE_UNIT_CHARS)
    spaced = re.sub(f"([{units}])", r" \1 ", value)
    tokens = re.sub(rf"[^\w\s{units}-]", " ", spaced, flags=re.UNICODE).casefold().split()
    return ["not" if t in ("never", "никогда") else t for t in tokens]


def _values_equivalent(a: str, b: str) -> bool:
    """Two claim values that agree, possibly in other words: equal token forms, or the shorter is a token-prefix of the longer and has 3+ tokens.

    A tail with a qualifier (except/only/until/кроме) is a different fact: a missed corroboration is cheaper than a false merge.
    """
    ta, tb = _value_tokens(a), _value_tokens(b)
    if ta == tb:
        return True
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if not short or long_[: len(short)] != short:
        return False
    tail = long_[len(short):]
    # A tail with a unit/scale/period word or qualifier changes the value ("5" vs "5 billion"); a plain noun after a number does not ("100" = "100 requests").
    if any(t in _VALUE_QUALIFIERS or _is_measure_token(t) for t in tail):
        return False
    return len(short) >= 3 or any(ch.isdigit() for ch in short[-1])


def _distinct_values(values: Iterable[str]) -> list[str]:
    """Cluster values by equivalence; one representative per cluster."""
    reps: list[str] = []
    for value in sorted(set(values)):
        if not any(_values_equivalent(value, rep) for rep in reps):
            reps.append(value)
    return reps


def _claim_id(locator: str = "", text: str = "") -> str:
    """Stable id: the same sentence from the same document gets the same id in every run (a random id meant quarantine never matched a later contradiction)."""
    if not text:
        from core.ids import new_id

        return new_id("claim")
    import hashlib

    key = _document_key(locator) + "\n" + " ".join(text.casefold().split())
    return "claim_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


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
