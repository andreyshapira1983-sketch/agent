"""Post-verifier confidence gate.

Berkeley MAST 2025 (Section 6) and Horvitz 1999 both argue that an agent
should pause and gather more evidence — not just deliver its draft — when
post-hoc confidence is low. Our :class:`core.verifier.Verifier` already
tags each claim chunk as ``verified`` / ``unverified`` /
``cited_but_unmatched`` / ``self_declared``, but the loop currently does
nothing with that distribution unless an unresolved-URL replan is
specifically warranted.

This module provides:

* :func:`compute_confidence` — a single pure scalar in ``[0.0, 1.0]``
  derived from a ``VerificationReport``. Verified chunks count fully;
  ``cited_but_unmatched`` (a citation that resolved to nothing — a fabricated
  attribution) count as a penalty, same as ``unverified``; ``self_declared``
  as zero factual support.
* :class:`ConfidenceGate` with :meth:`evaluate` returning a
  ``ConfidenceGateResult`` so the loop can choose to log a warning or to
  feed a synthetic ``ReplanTrigger`` back into the policy in a future
  stricter mode.

Today the loop only logs ``low_confidence_gate``. The threshold and the
penalty weights live here so a future ``replan_on_low_confidence`` flag
can flip behaviour without touching the loop's prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_DEFAULT_THRESHOLD = 0.45
_DEFAULT_MIN_TOTAL_CHUNKS = 2  # below this, signal is too noisy to gate on


def compute_confidence(report: Any) -> float:
    """Scalar confidence score derived from a ``VerificationReport``.

    Returns ``0.0`` when the report is missing or carries zero non-
    structural chunks.  A fully-verified report yields ``1.0``.
    """
    if report is None:
        return 0.0
    total = int(getattr(report, "total_chunks", 0) or 0)
    if total <= 0:
        return 0.0
    verified = int(getattr(report, "verified_chunks", 0) or 0)
    cited = int(getattr(report, "cited_but_unmatched_chunks", 0) or 0)
    unverified = int(getattr(report, "unverified_chunks", 0) or 0)
    # self_declared chunks count as neither support nor penalty.
    # cited_but_unmatched = a citation that resolved to NOTHING in the chain
    # (a fabricated attribution). It is unverified support with a false source,
    # so it carries the same penalty as `unverified` — never positive credit
    # (CORE-04; it used to earn +0.5, inflating confidence on fabricated cites).
    raw = (verified * 1.0) + (cited * -0.25) + (unverified * -0.25)
    return max(0.0, min(1.0, raw / total))


@dataclass(frozen=True)
class ConfidenceGateResult:
    """Outcome of one gate evaluation."""

    confidence: float
    threshold: float
    total_chunks: int
    triggered: bool  # True iff confidence < threshold AND chunks >= min_total
    chain_was_empty: bool
    fully_unverified: bool

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "confidence": round(self.confidence, 3),
            "threshold": self.threshold,
            "total_chunks": self.total_chunks,
            "triggered": self.triggered,
            "chain_was_empty": self.chain_was_empty,
            "fully_unverified": self.fully_unverified,
        }


class ConfidenceGate:
    """Decide whether the post-verifier confidence is high enough to ship."""

    def __init__(
        self,
        threshold: float = _DEFAULT_THRESHOLD,
        min_total_chunks: int = _DEFAULT_MIN_TOTAL_CHUNKS,
    ) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be in [0, 1]")
        if min_total_chunks < 0:
            raise ValueError("min_total_chunks must be >= 0")
        self.threshold = float(threshold)
        self.min_total_chunks = int(min_total_chunks)

    def evaluate(self, report: Any) -> ConfidenceGateResult:
        confidence = compute_confidence(report)
        total = int(getattr(report, "total_chunks", 0) or 0) if report else 0
        chain_was_empty = bool(getattr(report, "chain_was_empty", False)) if report else True
        fully_unverified = bool(getattr(report, "fully_unverified", False)) if report else False
        triggered = (
            report is not None
            and total >= self.min_total_chunks
            and confidence < self.threshold
        )
        return ConfidenceGateResult(
            confidence=confidence,
            threshold=self.threshold,
            total_chunks=total,
            triggered=triggered,
            chain_was_empty=chain_was_empty,
            fully_unverified=fully_unverified,
        )
