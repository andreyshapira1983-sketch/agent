"""Operator-facing conflict review for the Source Registry.

The low-level `ConflictResolver` detects obvious contradictions and marks
claims as `conflicted`. This module turns that raw signal into something an
operator can inspect: competing claims, source trust, claim confidence, and a
bounded recommendation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from core.knowledge_pipeline import ConflictRecord, ConflictResolver
from core.source_registry import ClaimRecord, SourceRecord, SourceRegistry


ConflictDecision = Literal["suggested", "needs_review"]


@dataclass(frozen=True)
class ConflictClaimView:
    claim_id: str
    source_id: str
    text: str
    claim_status: str
    claim_confidence: float
    source_type: str
    source_title: str
    source_locator: str
    source_trust: float
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "source_id": self.source_id,
            "text": self.text,
            "claim_status": self.claim_status,
            "claim_confidence": round(self.claim_confidence, 3),
            "source_type": self.source_type,
            "source_title": self.source_title,
            "source_locator": self.source_locator,
            "source_trust": round(self.source_trust, 3),
            "score": round(self.score, 3),
        }


@dataclass(frozen=True)
class ConflictSuggestion:
    conflict_id: str
    subject: str
    values: tuple[str, ...]
    decision: ConflictDecision
    winner_claim_id: str | None
    confidence: float
    reasons: tuple[str, ...]
    claims: tuple[ConflictClaimView, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "subject": self.subject,
            "values": list(self.values),
            "decision": self.decision,
            "winner_claim_id": self.winner_claim_id,
            "confidence": round(self.confidence, 3),
            "reasons": list(self.reasons),
            "claims": [claim.to_dict() for claim in self.claims],
        }


@dataclass(frozen=True)
class ConflictReviewReport:
    suggestions: tuple[ConflictSuggestion, ...]

    @property
    def conflict_count(self) -> int:
        return len(self.suggestions)

    @property
    def suggested_count(self) -> int:
        return sum(1 for item in self.suggestions if item.decision == "suggested")

    @property
    def needs_review_count(self) -> int:
        return sum(1 for item in self.suggestions if item.decision == "needs_review")

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_count": self.conflict_count,
            "suggested_count": self.suggested_count,
            "needs_review_count": self.needs_review_count,
            "suggestions": [item.to_dict() for item in self.suggestions],
        }

    def user_summary(self, *, limit: int = 10) -> str:
        if not self.suggestions:
            return "=== conflicts ===\n(no conflicts found)"

        lines = [
            "=== conflicts ===",
            (
                f"total={self.conflict_count} suggested={self.suggested_count} "
                f"needs_review={self.needs_review_count}"
            ),
        ]
        for suggestion in self.suggestions[: max(1, limit)]:
            winner = suggestion.winner_claim_id or "-"
            lines.append(
                f"  {suggestion.conflict_id} subject={suggestion.subject!r} "
                f"decision={suggestion.decision} winner={winner} "
                f"confidence={suggestion.confidence:.2f}"
            )
            for reason in suggestion.reasons:
                lines.append(f"    reason: {reason}")
            for claim in suggestion.claims:
                preview = " ".join(claim.text.split())
                if len(preview) > 140:
                    preview = preview[:137] + "..."
                lines.append(
                    f"    - {claim.claim_id} score={claim.score:.2f} "
                    f"claim={claim.claim_confidence:.2f} source={claim.source_trust:.2f} "
                    f"{claim.source_type}:{claim.source_locator} :: {preview}"
                )
        hidden = self.conflict_count - max(1, limit)
        if hidden > 0:
            lines.append(f"  ... {hidden} more conflict(s)")
        return "\n".join(lines)


class ConflictReview:
    """Build deterministic recommendations for already-extracted claims."""

    def __init__(
        self,
        *,
        resolver: ConflictResolver | None = None,
        claim_weight: float = 0.55,
        source_weight: float = 0.45,
        suggestion_margin: float = 0.12,
        min_winner_score: float = 0.62,
    ):
        self.resolver = resolver or ConflictResolver()
        self.claim_weight = claim_weight
        self.source_weight = source_weight
        self.suggestion_margin = suggestion_margin
        self.min_winner_score = min_winner_score

    def review(self, registry: SourceRegistry) -> ConflictReviewReport:
        resolved, raw_report = self.resolver.resolve(registry)
        claims_by_id = {claim.id: claim for claim in resolved.claims}
        sources_by_id = {source.id: source for source in resolved.sources}

        suggestions: list[ConflictSuggestion] = []
        for index, conflict in enumerate(raw_report.conflicts, start=1):
            claim_views = tuple(
                view
                for claim_id in conflict.claim_ids
                if (view := self._claim_view(
                    claims_by_id.get(claim_id),
                    sources_by_id,
                )) is not None
            )
            if len(claim_views) < 2:
                continue
            suggestions.append(
                self._suggestion(
                    conflict,
                    claim_views=claim_views,
                    conflict_id=f"conflict_{index:03d}",
                )
            )
        return ConflictReviewReport(tuple(suggestions))

    def _claim_view(
        self,
        claim: ClaimRecord | None,
        sources_by_id: dict[str, SourceRecord],
    ) -> ConflictClaimView | None:
        if claim is None:
            return None
        source = sources_by_id.get(claim.source_id)
        if source is None:
            return None
        score = (
            _bounded(claim.confidence) * self.claim_weight
            + _bounded(source.trust_level) * self.source_weight
        )
        return ConflictClaimView(
            claim_id=claim.id,
            source_id=claim.source_id,
            text=claim.text,
            claim_status=claim.status,
            claim_confidence=_bounded(claim.confidence),
            source_type=source.type,
            source_title=source.title,
            source_locator=source.locator,
            source_trust=_bounded(source.trust_level),
            score=_bounded(score),
        )

    def _suggestion(
        self,
        conflict: ConflictRecord,
        *,
        claim_views: tuple[ConflictClaimView, ...],
        conflict_id: str,
    ) -> ConflictSuggestion:
        ranked = tuple(sorted(
            claim_views,
            key=lambda item: (item.score, item.source_trust, item.claim_confidence),
            reverse=True,
        ))
        top = ranked[0]
        runner_up = ranked[1]
        margin = top.score - runner_up.score
        reasons = [
            f"values in conflict: {', '.join(conflict.values)}",
            (
                f"top score {top.score:.2f} vs next {runner_up.score:.2f} "
                f"(margin {margin:.2f})"
            ),
        ]
        decision: ConflictDecision = "needs_review"
        winner_claim_id: str | None = None
        confidence = max(0.0, min(0.60, top.score))
        if top.score >= self.min_winner_score and margin >= self.suggestion_margin:
            decision = "suggested"
            winner_claim_id = top.claim_id
            confidence = min(0.95, 0.65 + margin)
            reasons.append(
                "one claim has materially stronger source trust/confidence"
            )
        else:
            reasons.append(
                "scores are too close or too weak for automatic resolution"
            )

        return ConflictSuggestion(
            conflict_id=conflict_id,
            subject=conflict.subject,
            values=conflict.values,
            decision=decision,
            winner_claim_id=winner_claim_id,
            confidence=_bounded(confidence),
            reasons=tuple(reasons),
            claims=ranked,
        )


def _bounded(value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(1.0, number))
