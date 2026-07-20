"""Contextual memory-use policy.

Memory write policy answers "may this be saved?". This module answers the
next question: "may this saved record be used for the current role?" That is
the guardrail against poppy, out-of-place memory reuse.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.memory_policy import _query_tokens as _retrieval_query_tokens
from core.memory_policy import _tag_tokens as _retrieval_tag_tokens
from core.models import MemoryRecord
from core.role_router import RoleContext


QUARANTINE_TAGS = frozenset({
    "quarantine",
    "do-not-use",
    "wrong",
    "obsolete",
    "superseded",
    # A claim contradicted by another source stops being ordinary evidence
    # until an operator resolves it (MIR-047). Listing it here means every
    # retrieval site inherits the filter with no extra code.
    "conflicted",
    "temporary",
    "transient",
})

_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
_STOPWORDS = frozenset({
    "the", "and", "or", "for", "with", "what", "how", "why", "это",
    "что", "как", "или", "для", "про", "где", "когда", "меня",
})


@dataclass(frozen=True)
class KnowledgeUseDecision:
    record_id: str
    decision: str
    reasons: tuple[str, ...]
    tags: tuple[str, ...] = ()
    # Machine-readable companion to `reasons`. The prose is for a human
    # reading one decision; aggregating a trace off it would mean parsing
    # sentences, which is inferring a cause instead of being told it.
    code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "tags": list(self.tags),
            "code": self.code,
        }


@dataclass
class KnowledgeUseReport:
    role: str
    total: int
    allowed: list[MemoryRecord] = field(default_factory=list)
    decisions: list[KnowledgeUseDecision] = field(default_factory=list)

    @property
    def rejected_count(self) -> int:
        return len([d for d in self.decisions if d.decision == "reject"])

    @property
    def rejected_by(self) -> dict[str, int]:
        """Rejections aggregated by cause, for the retrieval trace.

        The three reject branches are distinct diagnoses — a type outside the
        role's scope, a record marked unusable, and a record that simply has
        nothing to do with the question. Collapsing them into one number (as
        `len(records) - len(allowed)` did) tells the reader to look in the
        wrong place two times out of three.
        """
        counts: dict[str, int] = {}
        for decision in self.decisions:
            if decision.decision != "reject":
                continue
            code = decision.code or "unclassified"
            counts[code] = counts.get(code, 0) + 1
        return counts

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "records_total": self.total,
            "records_allowed": len(self.allowed),
            "records_rejected": self.rejected_count,
            "allowed_ids": [r.id for r in self.allowed],
            "decisions": [d.to_dict() for d in self.decisions],
        }


class KnowledgeUsePolicy:
    """Filter persistent records before keyword retrieval."""

    def filter(
        self,
        records: list[MemoryRecord],
        *,
        role_context: RoleContext,
        question: str,
    ) -> KnowledgeUseReport:
        report = KnowledgeUseReport(role=role_context.role, total=len(records))
        q_tokens = _retrieval_query_tokens(question)
        allowed_tags = {t.casefold() for t in role_context.allowed_memory_tags}
        allowed_types = {t.casefold() for t in role_context.allowed_memory_types}

        for record in records:
            tags = tuple(t.casefold() for t in (record.tags or []) if t)
            tags_set = set(tags)
            tag_match_tokens = _retrieval_tag_tokens(tags)
            reasons: list[str] = []

            if record.type.casefold() not in allowed_types:
                report.decisions.append(KnowledgeUseDecision(
                    record_id=record.id,
                    decision="reject",
                    reasons=(f"type {record.type} not allowed for role {role_context.role}",),
                    tags=tags,
                    code="role_scope",
                ))
                continue

            blocked = tags_set & QUARANTINE_TAGS
            if blocked:
                report.decisions.append(KnowledgeUseDecision(
                    record_id=record.id,
                    decision="reject",
                    reasons=(f"blocked tag(s): {sorted(blocked)}",),
                    tags=tags,
                    code="quarantined",
                ))
                continue

            tag_overlap = tags_set & allowed_tags
            if tag_overlap:
                reasons.append(f"role tag overlap: {sorted(tag_overlap)}")

            # Allow when the question itself references one of the record's tags.
            # This surfaces episodic/reflection records when the user asks about
            # them by tag name (e.g. "reflection", "lesson").
            question_tag_match = q_tokens & tag_match_tokens
            if question_tag_match:
                reasons.append(f"question matches tags: {sorted(question_tag_match)[:4]}")

            content = record.content if isinstance(record.content, str) else str(record.content)
            token_overlap = q_tokens & _tokens(content)
            if token_overlap:
                reasons.append(f"question overlap: {sorted(token_overlap)[:8]}")

            # First-party operator preferences are allowed broadly, but
            # retrieval still does keyword scoring afterwards. This means
            # they are candidates, not guaranteed prompt injection.
            if "preference" in tags_set:
                reasons.append("operator preference candidate")

            if reasons:
                report.allowed.append(record)
                report.decisions.append(KnowledgeUseDecision(
                    record_id=record.id,
                    decision="allow",
                    reasons=tuple(reasons),
                    tags=tags,
                ))
                continue

            report.decisions.append(KnowledgeUseDecision(
                record_id=record.id,
                decision="reject",
                reasons=(f"not applicable to role {role_context.role}",),
                tags=tags,
                code="not_applicable",
            ))

        return report


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for token in _TOKEN_RE.findall(text or ""):
        lowered = token.casefold()
        if len(lowered) <= 1 or lowered in _STOPWORDS:
            continue
        out.add(lowered)
    return out
