"""Contextual memory-use policy.

Memory write policy answers "may this be saved?". This module answers the
next question: "may this saved record be used for the current role?" That is
the guardrail against poppy, out-of-place memory reuse.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.models import MemoryRecord
from core.role_router import RoleContext


QUARANTINE_TAGS = frozenset({
    "quarantine",
    "do-not-use",
    "wrong",
    "obsolete",
    "superseded",
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "tags": list(self.tags),
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
        q_tokens = _tokens(question)
        allowed_tags = {t.casefold() for t in role_context.allowed_memory_tags}
        allowed_types = {t.casefold() for t in role_context.allowed_memory_types}

        for record in records:
            tags = tuple(t.casefold() for t in (record.tags or []) if t)
            tags_set = set(tags)
            reasons: list[str] = []

            if record.type.casefold() not in allowed_types:
                report.decisions.append(KnowledgeUseDecision(
                    record_id=record.id,
                    decision="reject",
                    reasons=(f"type {record.type} not allowed for role {role_context.role}",),
                    tags=tags,
                ))
                continue

            blocked = tags_set & QUARANTINE_TAGS
            if blocked:
                report.decisions.append(KnowledgeUseDecision(
                    record_id=record.id,
                    decision="reject",
                    reasons=(f"blocked tag(s): {sorted(blocked)}",),
                    tags=tags,
                ))
                continue

            tag_overlap = tags_set & allowed_tags
            if tag_overlap:
                reasons.append(f"role tag overlap: {sorted(tag_overlap)}")

            # Allow when the question itself references one of the record's tags.
            # This surfaces episodic/reflection records when the user asks about
            # them by tag name (e.g. "reflection", "lesson").
            question_tag_match = q_tokens & tags_set
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
