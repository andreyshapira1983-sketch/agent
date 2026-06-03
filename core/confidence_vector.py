"""Decompose answer confidence into a three-axis vector.

The existing :func:`core.confidence_gate.compute_confidence` collapses
everything into one scalar, which hides *why* the agent is uncertain:
"is the evidence weak?", "is the answer self-contradicting?", "is the
answer drifting away from what was asked?". Berkeley MAST 2025
(Sections 6.2–6.4) and the operator's own spec call for separating
these axes so triage can target the right subsystem.

This module is **observational only** — it logs alongside the existing
scalar gate. The user-facing `Confidence: high|medium|low` rewrite in
:mod:`core.output_policy` is unchanged.

Axes:

* ``evidence_score`` — verification coverage. Reuses the same chunk
  accounting as :func:`compute_confidence` (verified=1.0,
  cited_but_unmatched=0.5, unverified=-0.25, self_declared=0) but is
  reported separately so it can be read independently.
* ``coherence_score`` — internal consistency. Drops when subsystem
  outcomes contradict each other (planner says done / verifier says
  fully unverified) — i.e. when
  :func:`core.subsystem_disagreement.detect_disagreements` fires.
* ``relevance_score`` — alignment between the answer and the user
  question. Deterministic Jaccard-style overlap on tokenised
  question vs. answer text after stopword removal. No LLM call.

The three combine into ``overall_confidence`` via a weighted geometric
mean, so a near-zero score on any axis collapses the overall — a
"weakest-link" semantics that matches operator intuition.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence


# Severity weight applied to disagreement events when computing
# coherence. High-severity events (planner_vs_verifier_full) hurt much
# more than a low-severity executor mismatch.
_SEVERITY_WEIGHT: dict[str, float] = {
    "high": 0.6,
    "medium": 0.3,
    "low": 0.1,
}

# Geometric-mean weights. Evidence carries the most signal; coherence
# is observational; relevance is heuristic so weighted lightly.
_W_EVIDENCE = 0.5
_W_COHERENCE = 0.3
_W_RELEVANCE = 0.2


# Conservative bilingual stopword set for relevance tokenisation. Short
# functional tokens add noise to Jaccard overlap.
_STOPWORDS_EN = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "to", "for", "with", "by", "from", "as",
    "and", "or", "but", "if", "then", "else", "this", "that", "these",
    "those", "it", "its", "you", "your", "we", "our", "they", "their",
    "do", "does", "did", "have", "has", "had", "can", "could", "would",
    "should", "will", "shall", "may", "might", "must", "not", "no",
    "yes", "what", "when", "where", "who", "whom", "why", "how",
    "which", "there", "here", "than", "so", "too", "very",
})
_STOPWORDS_RU = frozenset({
    "и", "в", "во", "не", "на", "я", "что", "тот", "это", "как",
    "по", "но", "они", "к", "у", "ты", "из", "за", "то", "же",
    "вы", "так", "его", "её", "ее", "мы", "был", "была", "было",
    "были", "есть", "будет", "или", "если", "только", "там", "тут",
    "ли", "бы", "о", "об", "для", "от", "до", "при", "со", "с",
    "вот", "ну", "да", "нет", "уже", "ещё", "еще",
})
_STOPWORDS = _STOPWORDS_EN | _STOPWORDS_RU

_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)


def _tokenise(text: str) -> set[str]:
    if not text:
        return set()
    out: set[str] = set()
    for m in _TOKEN_RE.findall(text.lower()):
        if len(m) < 3:
            continue
        if m in _STOPWORDS:
            continue
        out.add(m)
    return out


def evidence_score(report: Any) -> float:
    """Verification coverage as a 0..1 score.

    Mirrors the formula in :func:`core.confidence_gate.compute_confidence`
    but lives here so callers can read this axis without taking the
    full scalar dependency.
    """
    if report is None:
        return 0.0
    total = int(getattr(report, "total_chunks", 0) or 0)
    if total <= 0:
        return 0.0
    verified = int(getattr(report, "verified_chunks", 0) or 0)
    cited = int(getattr(report, "cited_but_unmatched_chunks", 0) or 0)
    unverified = int(getattr(report, "unverified_chunks", 0) or 0)
    raw = verified * 1.0 + cited * 0.5 + unverified * -0.25
    return max(0.0, min(1.0, raw / total))


def coherence_score(disagreements: Sequence[dict] | None) -> float:
    """1.0 minus the cumulative severity of subsystem disagreements,
    clamped to ``[0, 1]``.

    No disagreements -> 1.0. A single high-severity event drags the
    score down by ``_SEVERITY_WEIGHT['high']``.  Multiple events
    accumulate but the score never goes below 0.
    """
    if not disagreements:
        return 1.0
    penalty = 0.0
    for ev in disagreements:
        sev = ev.get("severity", "low")
        penalty += _SEVERITY_WEIGHT.get(sev, _SEVERITY_WEIGHT["low"])
    return max(0.0, min(1.0, 1.0 - penalty))


def relevance_score(question: str | None, answer: str | None) -> float:
    """Jaccard-style overlap between question and answer content tokens.

    Returns ``0.5`` when either side is empty after stopword removal —
    we cannot judge alignment in either direction, so we stay neutral
    rather than punish a short answer to a vague prompt.
    """
    q_tokens = _tokenise(question or "")
    a_tokens = _tokenise(answer or "")
    if not q_tokens or not a_tokens:
        return 0.5
    inter = q_tokens & a_tokens
    if not inter:
        return 0.0
    # Coverage of the question vocabulary by the answer is the more
    # operator-meaningful direction (did the answer address what was
    # asked?), so we use |q ∩ a| / |q| rather than full Jaccard.
    coverage = len(inter) / len(q_tokens)
    return max(0.0, min(1.0, coverage))


@dataclass(frozen=True)
class ConfidenceVector:
    """Three-axis confidence diagnosis plus a weighted overall score."""
    evidence_score: float
    coherence_score: float
    relevance_score: float
    overall_confidence: float

    def to_log_payload(self) -> dict[str, float]:
        return {
            "evidence_score": round(self.evidence_score, 3),
            "coherence_score": round(self.coherence_score, 3),
            "relevance_score": round(self.relevance_score, 3),
            "overall_confidence": round(self.overall_confidence, 3),
        }


def _weighted_geometric_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    # Use a small epsilon so a hard zero on any axis still computes
    # (otherwise log(0) blows up), but the result still collapses to a
    # very small number — preserving the "weakest-link" semantics.
    import math
    eps = 1e-3
    log_sum = 0.0
    weight_sum = 0.0
    for v, w in zip(values, weights):
        v_clamped = max(eps, min(1.0, v))
        log_sum += w * math.log(v_clamped)
        weight_sum += w
    if weight_sum == 0:
        return 0.0
    return max(0.0, min(1.0, math.exp(log_sum / weight_sum)))


def compute_vector(
    *,
    report: Any,
    disagreements: Sequence[dict] | None,
    question: str | None,
    answer: str | None,
) -> ConfidenceVector:
    e = evidence_score(report)
    c = coherence_score(disagreements)
    r = relevance_score(question, answer)
    overall = _weighted_geometric_mean(
        [e, c, r],
        [_W_EVIDENCE, _W_COHERENCE, _W_RELEVANCE],
    )
    return ConfidenceVector(
        evidence_score=e,
        coherence_score=c,
        relevance_score=r,
        overall_confidence=overall,
    )
