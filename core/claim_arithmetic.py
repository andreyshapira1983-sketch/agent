"""Deterministic evaluation of arithmetic claims against a key=value excerpt.

MIR-060 direction (b). The verifier's verdict tracks whether a citation
resolved, never whether the claim follows from what was cited, so
"the three values sum to 99" scores `verified` against `alpha=1 beta=2
gamma=3`. Three of the four false claims measured on the capability bench are a
sum, a count and a comparison — all decidable from the excerpt with no model at
all. This module decides them.

## Two rules it does not break

**It computes, it does not guess.** Every verdict here is arithmetic over
numbers parsed out of the excerpt. Where a claim's shape is not recognised the
answer is :data:`SILENT`, never `REFUTES` — a checker that refuses to judge
costs a missed defect, one that judges by resemblance costs a false accusation,
and only the second is unrecoverable.

**A refutation carries its working.** :class:`ClaimVerdict` returns the
expected value, the value the claim asserted, and the numbers the computation
used. A bare "no" tells the agent it was wrong; "sum is 6, not 99, over
alpha=1 beta=2 gamma=3" tells it what to change. That difference is the whole
point of the direction — the operator's criterion is that a label change means
the instrument got honest, and only a usable reason can make the next attempt
better.

## What it recognises

  sum          "the values sum to N",     "сумма ... N"
  average      "the average is N",        "среднее ... N"
  count        "defines N keys",          "N ключей"
  comparison   "X is smaller than Y",     "X больше Y"
  multiple     "X is N times Y",          "X в N раз больше Y"

Anything else is `SILENT`. The list grows by measurement — a shape is added
when a bench task needs it, not because it might occur.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Outcome = Literal["supports", "refutes", "silent"]

#: The shapes below are matched against a lower-cased claim. Russian forms sit
#: next to English ones because the operator writes in Russian and the model
#: answers in kind; a checker that only reads English would be silent on half
#: the traffic and would look like a checker that found nothing wrong.
#: Written-out numerals count as numerals. This is lexicon, not inference:
#: "three times alpha" states a factor exactly as "3 times alpha" does, and a
#: checker that reads only digits would be silent on the way people write.
_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "ноль": 0, "один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
}
_NUM = r"(-?\d+(?:[.,]\d+)?|" + "|".join(_WORD_NUMBERS) + r")"

_SUM_RE = re.compile(
    rf"\b(?:sum(?:s|med)?\s+to|total(?:s|ling)?\s+|сумм\w*\s+(?:равн\w*\s+)?)\D{{0,12}}{_NUM}",
    re.IGNORECASE)
_AVG_RE = re.compile(
    rf"\b(?:average|mean|средн\w*)\D{{0,24}}{_NUM}", re.IGNORECASE)
_COUNT_RE = re.compile(
    rf"\b(?:defines|contains|has|there\s+are|определя\w*|содерж\w*)\s+{_NUM}\s+"
    r"(?:keys?|entries|values?|ключ\w*|значен\w*|запис\w*)", re.IGNORECASE)
_TIMES_RE = re.compile(
    rf"\b([a-z_][a-z0-9_]*)\s+is\s+{_NUM}\s+times\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)
_CMP_RE = re.compile(
    r"\b([a-z_][a-z0-9_]*)\s+is\s+"
    r"(smaller|larger|greater|less|bigger|higher|lower)\s+than\s+"
    r"([a-z_][a-z0-9_]*)", re.IGNORECASE)

#: `<` for the words that mean "less". Everything else in `_CMP_RE` means more.
_LESS_WORDS = {"smaller", "less", "lower"}

_PAIR_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*(-?\d+(?:\.\d+)?)\s*$")


@dataclass(frozen=True)
class ClaimVerdict:
    """What the arithmetic says, and the working behind it."""

    outcome: Outcome
    code: str = ""
    expected: str = ""
    actual: str = ""
    explanation: str = ""
    computed_from: str = ""

    @property
    def refutes(self) -> bool:
        return self.outcome == "refutes"


SILENT = ClaimVerdict(outcome="silent")


def parse_pairs(excerpt: str) -> dict[str, float]:
    """Numeric `key=value` / `key: value` pairs, one per line.

    Deliberately strict: a line that is not exactly one pair is skipped rather
    than salvaged. A parser that guesses at half-structured text would feed
    wrong numbers into a check whose whole value is being right.
    """
    pairs: dict[str, float] = {}
    for line in (excerpt or "").splitlines():
        match = _PAIR_RE.match(line)
        if match:
            pairs[match.group(1).lower()] = float(match.group(2))
    return pairs


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _claimed(raw: str) -> float:
    word = _WORD_NUMBERS.get(raw.strip().lower())
    return float(word) if word is not None else float(raw.replace(",", "."))


def _working(pairs: dict[str, float]) -> str:
    return " ".join(f"{k}={_fmt(v)}" for k, v in pairs.items())


def _compare(
    *, code: str, expected: float, claimed: float, what: str,
    pairs: dict[str, float], subset: dict[str, float] | None = None,
) -> ClaimVerdict:
    working = _working(subset if subset is not None else pairs)
    if abs(expected - claimed) < 1e-9:
        return ClaimVerdict("supports", code=code, expected=_fmt(expected),
                            actual=_fmt(claimed), computed_from=working,
                            explanation=f"{what} is {_fmt(expected)}, as claimed")
    return ClaimVerdict(
        "refutes", code=code, expected=_fmt(expected), actual=_fmt(claimed),
        computed_from=working,
        explanation=f"{what} is {_fmt(expected)}, not {_fmt(claimed)}",
    )


def evaluate(claim: str, excerpt: str) -> ClaimVerdict:
    """Decide *claim* against *excerpt*, or stay `SILENT`.

    Order matters only where shapes overlap: `_TIMES_RE` is tried before
    `_CMP_RE` because "three times larger" contains a comparison word.
    """
    pairs = parse_pairs(excerpt)
    if not pairs:
        return SILENT
    text = (claim or "").strip()
    if not text:
        return SILENT

    values = list(pairs.values())

    match = _TIMES_RE.search(text)
    if match:
        left, factor, right = match.group(1).lower(), _claimed(match.group(2)), match.group(3).lower()
        if left in pairs and right in pairs:
            return _compare(code="multiple_mismatch", expected=pairs[left],
                            claimed=factor * pairs[right],
                            what=f"{left} against {_fmt(factor)} x {right}", pairs=pairs,
                            subset={left: pairs[left], right: pairs[right]})

    match = _CMP_RE.search(text)
    if match:
        left, word, right = match.group(1).lower(), match.group(2).lower(), match.group(3).lower()
        if left in pairs and right in pairs:
            wants_less = word in _LESS_WORDS
            holds = pairs[left] < pairs[right] if wants_less else pairs[left] > pairs[right]
            subset = {left: pairs[left], right: pairs[right]}
            if holds:
                return ClaimVerdict(
                    "supports", code="comparison", expected=word, actual=word,
                    computed_from=_working(subset),
                    explanation=f"{left}={_fmt(pairs[left])} is indeed {word} than "
                                f"{right}={_fmt(pairs[right])}")
            return ClaimVerdict(
                "refutes", code="comparison_false",
                expected=f"{left} is NOT {word} than {right}", actual=text,
                computed_from=_working(subset),
                explanation=f"{left}={_fmt(pairs[left])} and {right}={_fmt(pairs[right])}, "
                            f"so {left} is not {word} than {right}")

    match = _COUNT_RE.search(text)
    if match:
        return _compare(code="count_mismatch", expected=float(len(pairs)),
                        claimed=_claimed(match.group(1)),
                        what="the number of keys", pairs=pairs)

    match = _AVG_RE.search(text)
    if match:
        return _compare(code="average_mismatch", expected=sum(values) / len(values),
                        claimed=_claimed(match.group(1)),
                        what="the average", pairs=pairs)

    match = _SUM_RE.search(text)
    if match:
        return _compare(code="sum_mismatch", expected=sum(values),
                        claimed=_claimed(match.group(1)),
                        what="the sum", pairs=pairs)

    return SILENT
