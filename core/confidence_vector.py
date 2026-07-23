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
  cited_but_unmatched=-0.25, unverified=-0.25, self_declared=0) but is
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

from core.confidence_gate import compute_confidence


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
    # Request/framing verbs: they describe *how* something was asked, not
    # *what* about, so an answer never echoes them — counting them as
    # question content systematically depresses the coverage score.
    "tell", "explain", "show", "list", "find", "give", "check", "please",
    "describe", "need", "want", "make", "help", "my", "me",
})
_STOPWORDS_RU = frozenset({
    "и", "в", "во", "не", "на", "я", "что", "тот", "это", "как",
    "по", "но", "они", "к", "у", "ты", "из", "за", "то", "же",
    "вы", "так", "его", "её", "ее", "мы", "был", "была", "было",
    "были", "есть", "будет", "или", "если", "только", "там", "тут",
    "ли", "бы", "о", "об", "для", "от", "до", "при", "со", "с",
    "вот", "ну", "да", "нет", "уже", "ещё", "еще",
    # Interrogatives / determiners.
    "какой", "какая", "какие", "каком", "какую", "каких", "каким",
    "сколько", "почему", "зачем", "когда", "кто", "чей", "где",
    # Possessive / demonstrative pronouns.
    "мой", "моя", "мое", "моё", "мои", "моего", "моей", "моих", "моим",
    "твой", "ваш", "наш", "свой", "мне", "меня",
    # Common request/framing verbs (imperative + infinitive forms).
    "проверь", "проверить", "скажи", "сказать", "покажи", "показать",
    "объясни", "объяснить", "расскажи", "рассказать", "посмотри",
    "посмотреть", "найди", "найти", "опиши", "описать", "перечисли",
    "дай", "дать", "сделай", "сделать", "помоги", "помочь",
})
_STOPWORDS = _STOPWORDS_EN | _STOPWORDS_RU

_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)

# Morphological fuzzy-match tuning. Two tokens are treated as the same
# content word when they share a long common prefix — this collapses
# inflected forms ("репозиторий"/"репозитории", "problem"/"problems")
# that exact-token matching would miss. The thresholds are deliberately
# strict so unrelated words sharing a short Russian prefix ("про-",
# "пере-") or an English stem do not match spuriously.
_FUZZY_MIN_PREFIX = 4
_FUZZY_MIN_RATIO = 0.75


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

    Delegates to :func:`core.confidence_gate.compute_confidence` so the two
    axes can never drift apart; exposed separately so callers can read this
    axis without depending on the full scalar gate's semantics.
    """
    return compute_confidence(report)


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


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _same_word(q_tok: str, a_tok: str) -> bool:
    """Do two tokens denote the same content word up to inflection?

    True on an exact match, or when the tokens share a common prefix that
    is both long (>= ``_FUZZY_MIN_PREFIX`` chars) and covers most of the
    longer token (>= ``_FUZZY_MIN_RATIO``). This lets inflected forms
    ("проблема"/"проблемы", "problem"/"problems") count as one word
    without matching unrelated words that merely share a short prefix.
    """
    if q_tok == a_tok:
        return True
    cp = _common_prefix_len(q_tok, a_tok)
    if cp < _FUZZY_MIN_PREFIX:
        return False
    return cp / max(len(q_tok), len(a_tok)) >= _FUZZY_MIN_RATIO


def relevance_score(question: str | None, answer: str | None) -> float:
    """Morphology-aware overlap between question and answer content tokens.

    Returns ``0.5`` when either side is empty after stopword removal —
    we cannot judge alignment in either direction, so we stay neutral
    rather than punish a short answer to a vague prompt.

    Coverage is the fraction of question content words the answer
    addresses. Matching is fuzzy on a shared prefix (see :func:`_same_word`)
    so inflected forms — pervasive in Russian and common in English
    plurals — are not miscounted as misses, which previously pinned the
    score near ~0.3 even for on-topic answers.
    """
    q_tokens = _tokenise(question or "")
    a_tokens = _tokenise(answer or "")
    if not q_tokens or not a_tokens:
        return 0.5
    covered = 0
    for q_tok in q_tokens:
        if any(_same_word(q_tok, a_tok) for a_tok in a_tokens):
            covered += 1
    if not covered:
        return 0.0
    # Coverage of the question vocabulary by the answer is the more
    # operator-meaningful direction (did the answer address what was
    # asked?), so we use |covered| / |q| rather than full Jaccard.
    coverage = covered / len(q_tokens)
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
