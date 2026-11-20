"""Evidence support: how well the gathered sources back THIS answer; telemetry, not a gate.

    Three situations are kept apart. Evidence never required: applicable=False,
    score=None, reason="no_evidence_expected". Required and absent: applicable=True,
    score=0.0. Cited sources that resolve to nothing: applicable=True, score=0.0,
    citation_integrity_violation=True. Supported: score in (0, 1]. Applicability
    comes from core.low_evidence_policy.is_evidence_expected, the same function
    the enforcing layer uses. The signal is NOT connected to replan."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Below this an applicable score is reported as weak (kept for comparing old logs; gates a telemetry boolean only).
DEFAULT_WEAK_THRESHOLD = 0.45

#: Fewer claims than this and the ratio is too coarse to call weak.
DEFAULT_MIN_TOTAL_CHUNKS = 2


def compute_evidence_support(report: Any) -> float:
    """Fraction of the answer's claims that the gathered evidence backs.

    Verified and `dialogue_supported` claims count fully. `unverified` and
    `cited_but_unmatched` carry the same penalty; `self_declared` is neither."""
    if report is None:
        return 0.0
    total = int(getattr(report, "total_chunks", 0) or 0)
    if total <= 0:
        return 0.0
    verified = int(getattr(report, "verified_chunks", 0) or 0)
    dialogue = int(getattr(report, "dialogue_supported_chunks", 0) or 0)
    cited = int(getattr(report, "cited_but_unmatched_chunks", 0) or 0)
    unverified = int(getattr(report, "unverified_chunks", 0) or 0)
    # `user_asserted` earns no credit (the user's words confirm what was said, not that it is true) and no penalty.
    raw = ((verified + dialogue) * 1.0) + (cited * -0.25) + (unverified * -0.25)
    return max(0.0, min(1.0, raw / total))


@dataclass(frozen=True)
class EvidenceSupportResult:
    """Whether the question applies to this turn, and if so, the answer."""

    applicable: bool
    score: float | None
    reason: str
    citation_integrity_violation: bool = False
    total_chunks: int = 0
    verified_chunks: int = 0
    dialogue_supported_chunks: int = 0
    fabricated_citations: int = 0
    below_threshold: bool = False
    threshold: float = DEFAULT_WEAK_THRESHOLD
    chain_was_empty: bool = False
    fully_unverified: bool = False

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "applicable": self.applicable,
            "score": None if self.score is None else round(self.score, 3),
            "reason": self.reason,
            "citation_integrity_violation": self.citation_integrity_violation,
            "total_chunks": self.total_chunks,
            "verified_chunks": self.verified_chunks,
            "dialogue_supported_chunks": self.dialogue_supported_chunks,
            "fabricated_citations": self.fabricated_citations,
            "below_threshold": self.below_threshold,
            "threshold": self.threshold,
            "chain_was_empty": self.chain_was_empty,
            "fully_unverified": self.fully_unverified,
        }


def evaluate_evidence_support(
    report: Any,
    *,
    evidence_expected: bool,
    threshold: float = DEFAULT_WEAK_THRESHOLD,
    min_total_chunks: int = DEFAULT_MIN_TOTAL_CHUNKS,
) -> EvidenceSupportResult:
    """Report evidence support for one verified answer."""
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be in [0, 1]")
    if min_total_chunks < 0:
        raise ValueError("min_total_chunks must be >= 0")

    if report is None:
        return EvidenceSupportResult(
            applicable=False, score=None, reason="no_report", threshold=threshold
        )

    total = int(getattr(report, "total_chunks", 0) or 0)
    verified = int(getattr(report, "verified_chunks", 0) or 0)
    dialogue = int(getattr(report, "dialogue_supported_chunks", 0) or 0)
    fabricated = int(getattr(report, "cited_but_unmatched_chunks", 0) or 0)
    chain_was_empty = bool(getattr(report, "chain_was_empty", False))
    fully_unverified = bool(getattr(report, "fully_unverified", False))

    common = {
        "total_chunks": total,
        "verified_chunks": verified,
        "dialogue_supported_chunks": dialogue,
        "fabricated_citations": fabricated,
        "threshold": threshold,
        "chain_was_empty": chain_was_empty,
        "fully_unverified": fully_unverified,
    }

    if total <= 0:
        # Nothing was claimed, so nothing needed backing.
        return EvidenceSupportResult(
            applicable=False, score=None, reason="no_claims", **common
        )

    if not evidence_expected:
        # Situation A: reported as not applicable, not as a low score.
        return EvidenceSupportResult(
            applicable=False,
            score=None,
            reason="no_evidence_expected",
            citation_integrity_violation=fabricated > 0,
            **common,
        )

    score = compute_evidence_support(report)
    return EvidenceSupportResult(
        applicable=True,
        score=score,
        reason="measured",
        # Situation C is its own flag: a fabricated attribution is an integrity failure even when the ratio is good.
        citation_integrity_violation=fabricated > 0,
        below_threshold=(total >= min_total_chunks and score < threshold),
        **common,
    )
