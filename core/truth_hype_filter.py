"""Truth/Hype Filter — the first LEARNING antibody (правда vs шумиха).

The operator's formula: the agent must not merely INGEST text, it must judge
whether a piece of content carries substantive, checkable information ("truth")
or is empty marketing / hype ("шумиха") that should never become long-term
knowledge.

    Сначала научить и ПРОВЕРИТЬ, что усвоено — а не просто проглотить.

This module is a pure, deterministic POLICY. Given a piece of text it returns a
verdict (``substantive`` | ``hype``) plus the hype/substance signals it
observed. No LLM, no I/O, mutates nothing — O(n) over the text.

Design principles
-----------------
* No LLM calls, no I/O — regex + counting heuristics, deterministic and O(n).
* Conservative: ordinary factual text is ``substantive``. A ``hype`` verdict
  requires BOTH a clear excess of promotional language AND a lack of concrete,
  checkable substance (numbers, named entities, sources, causal structure).
  This asymmetry means the filter never silently drops real information just
  because it reads confidently — it only flags content that is loud yet empty.
* The filter only DECIDES (substantive/hype) + explains. It never rewrites or
  deletes text; callers (e.g. the knowledge write policy) act on the verdict.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


TruthHypeVerdict = Literal["substantive", "hype"]


# ---------------------------------------------------------------------------
# Lexicons
# ---------------------------------------------------------------------------

# Promotional / marketing terms (EN + RU). Matched case-insensitively as whole
# phrases. Curated to avoid ordinary technical words — every entry is something
# that signals salesmanship rather than information.
_HYPE_TERMS: tuple[str, ...] = (
    # English
    "revolutionary", "revolutionize", "revolutionise", "game-changing",
    "game changer", "game-changer", "cutting-edge", "cutting edge",
    "world-class", "world class", "best-in-class", "best in class",
    "next-generation", "next generation", "state-of-the-art",
    "state of the art", "unprecedented", "seamless", "seamlessly",
    "effortless", "effortlessly", "unparalleled", "unmatched", "ultimate",
    "magical", "mind-blowing", "mind blowing", "disruptive", "synergy",
    "synergies", "paradigm shift", "blazing-fast", "blazing fast",
    "lightning-fast", "lightning fast", "supercharge", "supercharged",
    "transform your", "must-have", "must have", "no-brainer", "turnkey",
    "best ever", "number one", "industry-leading", "industry leading",
    "groundbreaking", "second to none", "look no further",
    # Russian
    "революционный", "революционн", "прорывной", "прорывн", "инновационный",
    "инновационн", "не имеющий аналогов", "не имеет аналогов",
    "лучший в мире", "лучшее в мире", "передовой", "передов", "бесшовный",
    "бесшовн", "молниеносный", "молниеносн", "гарантированно", "гарантируем",
    "эксклюзивный", "эксклюзивн", "мощнейший", "потрясающий", "потрясающ",
    "легендарный", "уникальный", "уникальн", "непревзойдённый",
    "непревзойденный", "киллер-фича", "синергия", "меняет правила игры",
)

# Superlative / absolutist intensifiers (separate count from named hype terms).
_SUPERLATIVE = re.compile(
    r"\b(best|greatest|fastest|smartest|easiest|most\s+\w+|always|never|"
    r"everyone|nobody|guaranteed|100%|самый\s+\w+|всегда|никогда|каждый|"
    r"любой|абсолютно)\b",
    re.IGNORECASE,
)

# Substance signals — concrete, checkable content.
_NUMBER = re.compile(r"\b\d+(?:[.,]\d+)?\b")
# A capitalised token that is NOT at the start of the text (a proper noun / API
# name / product), or an identifier-looking token (snake/camel/dotted).
_PROPER_NOUN = re.compile(r"(?<!^)(?<![.!?]\s)\b[A-ZА-Я][A-Za-zА-Яа-я0-9]{2,}\b")
_IDENTIFIER = re.compile(r"\b\w+(?:[._][\w]+)+\b|\b[a-z]+[A-Z]\w+\b")
_CITATION = re.compile(
    r"https?://|\[[^\]]+\]|according to|as documented|per the|"
    r"согласно|по данным|как указано|см\.\s",
    re.IGNORECASE,
)
_CAUSAL = re.compile(
    r"\b(because|therefore|hence|thus|as a result|due to|so that|"
    r"потому что|поэтому|следовательно|так как|из-за|в результате)\b",
    re.IGNORECASE,
)

# Thresholds (deterministic). A verdict of "hype" requires the hype score to
# clear the bar AND the substance score to stay at/under the floor.
_HYPE_THRESHOLD = 0.34
_SUBSTANCE_FLOOR = 0.25
_MAX_SCAN_CHARS = 4096   # ReDoS / cost guard


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TruthHypeSignals:
    """The raw, observable signals extracted from a piece of text."""

    word_count: int = 0
    hype_terms: tuple[str, ...] = ()
    superlative_count: int = 0
    exclamation_count: int = 0
    has_number: bool = False
    has_proper_noun: bool = False
    has_identifier: bool = False
    has_citation: bool = False
    has_causal: bool = False

    @property
    def substance_markers(self) -> int:
        return sum((
            self.has_number,
            self.has_proper_noun,
            self.has_identifier,
            self.has_citation,
            self.has_causal,
        ))

    def to_dict(self) -> dict[str, object]:
        return {
            "word_count": self.word_count,
            "hype_terms": list(self.hype_terms),
            "superlative_count": self.superlative_count,
            "exclamation_count": self.exclamation_count,
            "has_number": self.has_number,
            "has_proper_noun": self.has_proper_noun,
            "has_identifier": self.has_identifier,
            "has_citation": self.has_citation,
            "has_causal": self.has_causal,
            "substance_markers": self.substance_markers,
        }


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TruthHypeOutcome:
    verdict: TruthHypeVerdict
    hype_score: float
    substance_score: float
    signals: TruthHypeSignals
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_hype(self) -> bool:
        return self.verdict == "hype"

    @property
    def is_substantive(self) -> bool:
        return self.verdict == "substantive"

    def to_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "hype_score": round(self.hype_score, 3),
            "substance_score": round(self.substance_score, 3),
            "signals": self.signals.to_dict(),
            "reasons": list(self.reasons),
        }


# ---------------------------------------------------------------------------
# Extraction + scoring
# ---------------------------------------------------------------------------

def _extract_signals(text: str) -> TruthHypeSignals:
    lowered = text.casefold()
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    word_count = len(words)

    matched: list[str] = []
    for term in _HYPE_TERMS:
        if term in lowered:
            matched.append(term)

    return TruthHypeSignals(
        word_count=word_count,
        hype_terms=tuple(matched),
        superlative_count=len(_SUPERLATIVE.findall(text)),
        exclamation_count=text.count("!"),
        has_number=bool(_NUMBER.search(text)),
        has_proper_noun=bool(_PROPER_NOUN.search(text)),
        has_identifier=bool(_IDENTIFIER.search(text)),
        has_citation=bool(_CITATION.search(text)),
        has_causal=bool(_CAUSAL.search(text)),
    )


def _hype_score(signals: TruthHypeSignals) -> float:
    """0..1 promotional-language density. Saturating, so a short slogan with two
    hype terms scores high while a long technical text with one stray buzzword
    does not."""
    words = max(signals.word_count, 1)
    # Weighted promotional load: named hype terms dominate, superlatives and
    # exclamations add lesser pressure.
    load = (
        2.0 * len(signals.hype_terms)
        + 1.0 * signals.superlative_count
        + 0.5 * signals.exclamation_count
    )
    # Normalise per ~12 words so density (not raw length) drives the score.
    density = load / (words / 12.0 + 1.0)
    return max(0.0, min(1.0, density))


def _substance_score(signals: TruthHypeSignals) -> float:
    """0..1 concrete-content score from the number of distinct substance markers
    present (number, proper noun, identifier, citation, causal structure)."""
    return max(0.0, min(1.0, signals.substance_markers / 3.0))


def _reasons(signals: TruthHypeSignals, hype: float, substance: float) -> tuple[str, ...]:
    out: list[str] = []
    if signals.hype_terms:
        out.append(f"hype terms: {', '.join(signals.hype_terms[:5])}")
    if signals.superlative_count:
        out.append(f"superlatives x{signals.superlative_count}")
    if signals.exclamation_count:
        out.append(f"exclamations x{signals.exclamation_count}")
    if signals.substance_markers:
        present = [
            name for name, flag in (
                ("number", signals.has_number),
                ("proper_noun", signals.has_proper_noun),
                ("identifier", signals.has_identifier),
                ("citation", signals.has_citation),
                ("causal", signals.has_causal),
            ) if flag
        ]
        out.append(f"substance: {', '.join(present)}")
    out.append(f"hype_score={hype:.2f} substance_score={substance:.2f}")
    return tuple(out)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate(text: str) -> TruthHypeOutcome:
    """Classify *text* as ``substantive`` or ``hype``.

    Pure and deterministic — no LLM, no I/O. Empty / non-string input is
    treated as ``substantive`` (nothing to flag). A ``hype`` verdict requires
    BOTH a high promotional density AND a low substance score, so confidently
    written but checkable text is never dropped.
    """
    if not isinstance(text, str) or not text.strip():
        return TruthHypeOutcome(
            verdict="substantive",
            hype_score=0.0,
            substance_score=0.0,
            signals=TruthHypeSignals(),
            reasons=("empty input; nothing to flag",),
        )

    scan = text[:_MAX_SCAN_CHARS]
    signals = _extract_signals(scan)
    hype = _hype_score(signals)
    substance = _substance_score(signals)

    verdict: TruthHypeVerdict = (
        "hype"
        if hype >= _HYPE_THRESHOLD and substance <= _SUBSTANCE_FLOOR
        else "substantive"
    )
    return TruthHypeOutcome(
        verdict=verdict,
        hype_score=hype,
        substance_score=substance,
        signals=signals,
        reasons=_reasons(signals, hype, substance),
    )


def is_hype(text: str) -> bool:
    """Convenience boolean: True iff *text* is classified as empty hype."""
    return evaluate(text).is_hype
