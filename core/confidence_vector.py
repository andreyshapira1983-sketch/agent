"""Three-axis answer confidence vector (evidence, coherence, relevance) combined into an overall score by a weighted geometric mean (weakest link). Observational only: the user-facing Confidence rewrite in core.output_policy is unchanged."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from core.evidence_support import compute_evidence_support

# Severity weight per disagreement event for coherence; high severity (planner_vs_verifier_full) hurts most.
_SEVERITY_WEIGHT: dict[str, float] = {
    "high": 0.6,
    "medium": 0.3,
    "low": 0.1,
}

# Geometric-mean weights: evidence strongest, coherence observational, relevance heuristic and light.
_W_EVIDENCE = 0.5
_W_COHERENCE = 0.3
_W_RELEVANCE = 0.2


# Conservative bilingual stopword set; short functional tokens add noise to overlap.
_STOPWORDS_EN = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "to", "for", "with", "by", "from", "as",
    "and", "or", "but", "if", "then", "else", "this", "that", "these",
    "those", "it", "its", "you", "your", "we", "our", "they", "their",
    "do", "does", "did", "have", "has", "had", "can", "could", "would",
    "should", "will", "shall", "may", "might", "must", "not", "no",
    "yes", "what", "when", "where", "who", "whom", "why", "how",
    "which", "there", "here", "than", "so", "too", "very",
    # Request/framing verbs say how something was asked, not what about; counting them as question content depresses coverage.
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
    "какой", "какая", "какие", "каком", "какую", "каких", "каким",
    "сколько", "почему", "зачем", "когда", "кто", "чей", "где",
    "мой", "моя", "мое", "моё", "мои", "моего", "моей", "моих", "моим",
    "твой", "ваш", "наш", "свой", "мне", "меня",
    "проверь", "проверить", "скажи", "сказать", "покажи", "показать",
    "объясни", "объяснить", "расскажи", "рассказать", "посмотри",
    "посмотреть", "найди", "найти", "опиши", "описать", "перечисли",
    "дай", "дать", "сделай", "сделать", "помоги", "помочь",
})
_STOPWORDS = _STOPWORDS_EN | _STOPWORDS_RU

# Hyphenated words stay whole: splitting "что-нибудь" leaves an orphaned particle no answer could cover.
_TOKEN_RE = re.compile(r"[\w']+(?:-[\w']+)*", re.UNICODE)

# Indefinite pronouns are placeholders for any referent; a question built on one asks for a kind of reply. Russian forms are matched by pattern, English ones are a closed set.
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

# Morphological fuzzy match: tokens sharing a long common prefix are the same word (inflection); thresholds are strict so short shared prefixes ("про-", "пере-") do not match.
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
    """Question tokens that name a topic: an indefinite pronoun and its two satellites (the verb before, the descriptor after) are dropped structurally, before the stopword filter."""
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
    """Verification coverage as 0..1; delegates to compute_evidence_support so the axes cannot drift. A bare ratio: it conflates "no evidence owed" with "owed and missing" (use evaluate_evidence_support for that)."""
    return compute_evidence_support(report)


def coherence_score(disagreements: Sequence[dict] | None) -> float:
    """1.0 minus the cumulative severity of subsystem disagreements, clamped to [0, 1]."""
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
    """Do two tokens denote the same content word up to inflection?"""
    if q_tok == a_tok:
        return True
    cp = _common_prefix_len(q_tok, a_tok)
    if cp < _FUZZY_MIN_PREFIX:
        return False
    return cp / max(len(q_tok), len(a_tok)) >= _FUZZY_MIN_RATIO


def relevance_score(question: str | None, answer: str | None) -> float:
    """Morphology-aware overlap between question and answer content tokens."""
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
    # Coverage of the question vocabulary by the answer (|covered| / |q|), not full Jaccard: did the answer address what was asked.
    coverage = covered / len(q_tokens)
    return max(0.0, min(1.0, coverage))


def _script_of(tokens: Sequence[str]) -> str:
    """Dominant writing system of a token list: latin, cyrillic or none; only letters vote."""
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
    """Whether word coverage can mean "the answer addressed the question"."""
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
    """Three-axis confidence diagnosis plus a weighted overall score; relevance_score is None exactly when relevance_applicable is False (0.0 would confuse a miss with different alphabets)."""
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
    # Small epsilon so a zero axis still computes (log(0)) yet collapses the result: weakest-link semantics.
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
    judged_relevance: float | None = None,
) -> ConfidenceVector:
    """``judged_relevance``: score from the relevance judge (core/relevance_judge.py); when present it replaces word coverage."""
    e = evidence_score(report)
    c = coherence_score(disagreements)
    if judged_relevance is not None:
        applicable, r = True, max(0.0, min(1.0, float(judged_relevance)))
    else:
        applicable = relevance_applicable(question, answer)
        r = relevance_score(question, answer) if applicable else None
    if applicable:
        overall = _weighted_geometric_mean(
            [e, c, r or 0.0],
            [_W_EVIDENCE, _W_COHERENCE, _W_RELEVANCE],
        )
    else:
        # An inapplicable axis is dropped, not defaulted (0.5 invents data, 0.0 punishes); the rest are renormalised.
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
