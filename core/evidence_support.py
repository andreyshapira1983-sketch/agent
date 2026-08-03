"""Evidence support — how well the gathered sources back THIS answer; telemetry, not a gate.

Formerly `core/confidence_gate.py`, and the rename is the point. The old
module computed a single scalar it called *confidence* and, below a threshold,
logged `low_confidence_gate`. Measurement showed the scalar was not measuring
confidence at all (`docs/audit/SENSOR_SIGNAL_MEASUREMENT.md`): it collapsed
three different situations into almost the same zero.

| situation | old score | what it actually means |
|---|---|---|
| A — evidence was never required (general knowledge) | **0.000** | nothing to support; the question is not applicable |
| B — evidence was required and is absent | 0.000 | a real deficiency |
| C — the answer cited sources that resolve to nothing | 0.000 | a deficiency *and* an integrity violation |

`self_declared` chunks count as neither support nor penalty, so an honest
general-knowledge answer scores exactly 0.0 — the same number as an answer whose
every citation was fabricated. On real traffic that was not a corner case:
**11 of 12 firings** were case A, and on **6 of those 12** the enforcement layer
had already recorded `no_evidence_expected` for the same turn. The two layers
were disagreeing about whether evidence was owed at all.

That is a defect in the *meaning* of the value, not in the 0.45 threshold, so
the fix is a different return type rather than a different number:

* **A** → ``applicable=False, score=None, reason="no_evidence_expected"``.
  Not a low score. **No score**, because the question does not apply.
* **B** → ``applicable=True, score=0.0``.
* **C** → ``applicable=True, score=0.0, citation_integrity_violation=True``.
* supported → ``applicable=True, score`` in ``(0, 1]``.

Applicability is decided by the same :func:`core.low_evidence_policy.is_evidence_expected`
the enforcing layer uses, so the observer and the enforcer can no longer hold
opposite opinions about whether this turn owed evidence.

**Still observational.** Operator ruling of 2026-07-27: this signal is NOT
connected to replan. Measured cost of doing so on real traffic was +86% model
calls to re-run turns that were mostly honest general-knowledge answers, and a
signal present on 86% of turns separates almost nothing. It stays telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Below this, an *applicable* support score is reported as weak. Retained from
#: the previous module so historical log comparisons stay meaningful; it gates
#: a boolean in telemetry, never behaviour.
DEFAULT_WEAK_THRESHOLD = 0.45

#: Fewer claims than this and the ratio is too coarse to call weak.
DEFAULT_MIN_TOTAL_CHUNKS = 2


def compute_evidence_support(report: Any) -> float:
    """Fraction of the answer's claims that the gathered evidence backs.

    Verified claims count fully. `dialogue_supported` counts too (issue #119):
    a claim about this session's own exchange is backed by the transcript, and
    the truncation gate already counts it — an observer that did not would
    report "evidence required and absent" for exactly the self-analysis turns
    that fix was about. `unverified` and `cited_but_unmatched` carry the same
    penalty; a citation resolving to nothing is unverified support wearing a
    false source, never partial credit. `self_declared` is neither — which is
    why the *applicability* question above has to be asked separately.
    """
    if report is None:
        return 0.0
    total = int(getattr(report, "total_chunks", 0) or 0)
    if total <= 0:
        return 0.0
    verified = int(getattr(report, "verified_chunks", 0) or 0)
    dialogue = int(getattr(report, "dialogue_supported_chunks", 0) or 0)
    cited = int(getattr(report, "cited_but_unmatched_chunks", 0) or 0)
    unverified = int(getattr(report, "unverified_chunks", 0) or 0)
    # `user_asserted` earns NO credit here (operator ruling 2026-08-03,
    # MIR-028): the user's words confirm what was said, not that it is true,
    # so an answer built of user-echo citations must not measure as backed.
    # No penalty either — nothing was fabricated; the chunk simply does not
    # count toward the numerator, so an all-echo answer scores 0.0.
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
    """Report evidence support for one verified answer.

    ``evidence_expected`` comes from
    :func:`core.low_evidence_policy.is_evidence_expected` — the same call the
    enforcing layer makes. When it is false the score is ``None``: a turn that
    owed no evidence has no support ratio, and reporting 0.0 for it is the
    error this module was rewritten to stop making.
    """
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
        # Situation A. Reported as not-applicable rather than as a low score —
        # the distinction this module exists to make.
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
        # Situation C is orthogonal to the score: a fabricated attribution is an
        # integrity failure even when other claims are well supported, so it is
        # its own flag rather than a number that a good ratio could hide.
        citation_integrity_violation=fabricated > 0,
        below_threshold=(total >= min_total_chunks and score < threshold),
        **common,
    )
