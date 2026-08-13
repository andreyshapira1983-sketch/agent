"""Decompose answer confidence into a three-axis vector.

The existing :func:`core.evidence_support.compute_evidence_support` collapses
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
  accounting as :func:`compute_evidence_support` (verified=1.0,
  cited_but_unmatched=-0.25, unverified=-0.25, self_declared=0) but is
  reported separately so it can be read independently.
* ``coherence_score`` — internal consistency. Drops when subsystem
  outcomes contradict each other (planner says done / verifier says
  fully unverified) — i.e. when
  :func:`core.subsystem_disagreement.detect_disagreements` fires.
* ``relevance_score`` — alignment between the answer and the user
  question. Deterministic coverage of the question's TOPIC tokens by the
  answer's content tokens (stopwords, indefinite pronouns and their
  satellites removed). No LLM call. Applicability is asked before the
  value: cross-script pairs and topicless prompts are not measured.

The three combine into ``overall_confidence`` via a weighted geometric
mean, so a near-zero score on any axis collapses the overall — a
"weakest-link" semantics that matches operator intuition.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from core.evidence_support import compute_evidence_support

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
    "ли", "бы", "о", "об", "про", "для", "от", "до", "при", "со", "с",
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

# Hyphenated words stay whole. Splitting on the hyphen used to shred
# "что-нибудь" into "что" (stopword) + "нибудь", and the orphaned particle
# then passed for question content that no answer could ever cover.
_TOKEN_RE = re.compile(r"[\w']+(?:-[\w']+)*", re.UNICODE)

# Indefinite pronouns: placeholders for "any referent at all". A question
# built on one asks for a KIND of reply, not about a thing in the world.
# Russian forms are stem-suffix products, so a pattern beats a list; the
# English ones are a closed set.
_INDEFINITE_RU_RE = re.compile(
    r"^(что|чего|чему|чем|чём|кто|кого|кому|кем|ком"
    r"|как|куда|где|когда"
    r"|какой|какая|какое|какие|какого|какую|каких|каким|какими"
    r"|чё|че)-(нибудь|либо|то|нить)$"
)
_INDEFINITE_EN = frozenset({
    "something", "anything", "someone", "anyone", "somebody", "anybody",
    "whatever", "whatsoever",
})


def _is_indefinite(token: str) -> bool:
    return token in _INDEFINITE_EN or bool(_INDEFINITE_RU_RE.match(token))

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


def _question_topic_tokens(question: str) -> set[str]:
    """Question tokens that name a TOPIC — what coverage is allowed to count.

    An indefinite pronoun is a placeholder with two satellites: the verb
    governing it right before («посоветуй что-нибудь», "give me anything")
    and the descriptor right after («что-нибудь умное», "something smart") —
    the verb frames the request and the descriptor describes the desired
    ANSWER; neither names a subject the answer must mention. All three are
    dropped before the usual stopword filter. The drop is structural — any
    word in those slots is excluded, so no list of request verbs or
    adjectives has to be maintained phrase by phrase.
    """
    if not question:
        return set()
    ordered = _TOKEN_RE.findall(question.lower())
    dropped: set[int] = set()
    for i, tok in enumerate(ordered):
        if _is_indefinite(tok):
            dropped.update((i - 1, i, i + 1))
    kept = (t for i, t in enumerate(ordered) if i not in dropped)
    return {t for t in kept if len(t) >= 3 and t not in _STOPWORDS}


def evidence_score(report: Any) -> float:
    """Verification coverage as a 0..1 score.

    Delegates to :func:`core.evidence_support.compute_evidence_support` so the
    two axes can never drift apart; exposed separately so callers can read this
    axis without depending on the applicability question that module also asks.

    Note this axis is a bare ratio and therefore still conflates "no evidence
    was owed" with "evidence was owed and is missing". Callers that need that
    distinction must read `evaluate_evidence_support`, not this number.
    """
    return compute_evidence_support(report)


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

    Coverage is the fraction of question TOPIC words the answer
    addresses (see :func:`_question_topic_tokens` — indefinite pronouns and
    their descriptors are not topics). Matching is fuzzy on a shared prefix
    (see :func:`_same_word`) so inflected forms — pervasive in Russian and
    common in English plurals — are not miscounted as misses, which
    previously pinned the score near ~0.3 even for on-topic answers.
    """
    q_tokens = _question_topic_tokens(question or "")
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


def _script_of(tokens: Sequence[str]) -> str:
    """Dominant writing system of a token list: ``latin``, ``cyrillic`` or ``none``.

    Only letters vote. Digits, punctuation and code identifiers are shared by
    both sides and would blur the distinction this exists to draw.
    """
    latin = cyrillic = 0
    for token in tokens:
        for ch in token:
            if "a" <= ch.lower() <= "z":
                latin += 1
            elif "а" <= ch.lower() <= "я" or ch.lower() == "ё":
                cyrillic += 1
    if latin == 0 and cyrillic == 0:
        return "none"
    return "latin" if latin >= cyrillic else "cyrillic"


def relevance_applicable(question: str | None, answer: str | None) -> bool:
    """Whether word coverage can mean "the answer addressed the question".

    MEASURED 2026-08-10: the same Russian answer scored 0.009 against an English
    question and 0.421 against the same question in Russian. The measurement did
    not change its mind about the answer — it changed alphabets. The operator
    writes in Latin script and transliteration and reads Cyrillic answers, so
    the low score was not a finding about the answer, it was a finding about the
    keyboard, and it was being reported as the former.

    Coverage counts shared word forms. Between writing systems there are almost
    none, so the number is not a low relevance — it is no measurement at all,
    and the honest report of a measurement that did not happen is that it did
    not happen.

    MEASURED 2026-08-13, same class, new form: «скажи что-нибудь умное» scored
    0.0 against an on-topic reply and the operator was told the answer «может
    отвечать не на заданный вопрос». The question names no topic — «умное»
    describes the reply being requested, «что-нибудь» is a placeholder — so
    there is nothing for coverage to cover and the zero was, again, not a
    finding about the answer. Applicability therefore asks TWO questions,
    both before the value: same writing system, and does the question name a
    topic at all (:func:`_question_topic_tokens`).
    """
    q_topic = _question_topic_tokens(question or "")
    if not q_topic:
        return False
    q_script = _script_of(sorted(q_topic))
    a_script = _script_of(_tokenise(answer or ""))
    if q_script == "none" or a_script == "none":
        return False
    return q_script == a_script


@dataclass(frozen=True)
class ConfidenceVector:
    """Three-axis confidence diagnosis plus a weighted overall score.

    ``relevance_score`` is ``None`` exactly when ``relevance_applicable`` is
    False. The pair is deliberately not collapsed into a single number: a
    consumer that sees 0.0 cannot tell "the answer missed the question" from
    "the question and the answer are written in different alphabets", and one
    of those is an accusation while the other is a shrug.
    """
    evidence_score: float
    coherence_score: float
    relevance_score: float | None
    overall_confidence: float
    relevance_applicable: bool = True

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "evidence_score": round(self.evidence_score, 3),
            "coherence_score": round(self.coherence_score, 3),
            "relevance_score": (
                None if self.relevance_score is None
                else round(self.relevance_score, 3)
            ),
            "relevance_applicable": self.relevance_applicable,
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
    for v, w in zip(values, weights, strict=False):
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
    applicable = relevance_applicable(question, answer)
    r = relevance_score(question, answer) if applicable else None
    if applicable:
        overall = _weighted_geometric_mean(
            [e, c, r or 0.0],
            [_W_EVIDENCE, _W_COHERENCE, _W_RELEVANCE],
        )
    else:
        # Dropped, not defaulted. Substituting 0.5 would invent an observation
        # and substituting 0.0 would let an unmeasured axis punish the answer;
        # the remaining axes are renormalised so they still mean what they say.
        overall = _weighted_geometric_mean(
            [e, c], [_W_EVIDENCE, _W_COHERENCE]
        )
    return ConfidenceVector(
        evidence_score=e,
        coherence_score=c,
        relevance_score=r,
        overall_confidence=overall,
        relevance_applicable=applicable,
    )
