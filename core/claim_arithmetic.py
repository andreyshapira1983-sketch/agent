"""Deterministic evaluation of arithmetic claims against a key=value excerpt.

**It computes, it does not guess.** Every verdict here is arithmetic over
numbers parsed out of the excerpt. Where a claim's shape is not recognised
the answer is :data:`SILENT`, never `REFUTES` — a checker that refuses to
judge costs a missed defect, one that judges by resemblance costs a false
accusation, and only the second is unrecoverable.

**A refutation carries its working.** :class:`ClaimVerdict` returns the
expected value, the value the claim asserted, and the numbers the
computation used. A bare "no" tells the agent it was wrong; "sum is 6, not
99, over alpha=1 beta=2 gamma=3" tells it what to change. That difference is
the whole point of the direction — the operator's criterion is that a label
change means the instrument got honest, and only a usable reason can make
the next attempt better.
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

#: Разность прогона: `passed = total - failed`. Это ЛЕКСИКОН, а не вывод — в
#: отчётах о прогоне отношение фиксированное, и «117 tests passed» при
#: `tests_total=120, tests_failed=3` ровно так и вычисляется. Слепой поиск
#: «какой-нибудь пары, чья разность равна заявленному» был бы суждением по
#: сходству, чего этот модуль не делает; здесь названы и слово, и ключи.
_PASSED_RE = re.compile(
    rf"\b(?:{_NUM}\s+(?:tests?\s+)?(?:passed|passing)"
    rf"|(?:прошл\w*|пройден\w*)\s+{_NUM}"
    rf"|(?:reports?|records?)\s+{_NUM}\s+passed)", re.IGNORECASE)

#: Сравнение ключа с ЛИТЕРАЛОМ: «version below 3.0», «coverage is above 0.5»,
#: «версия ниже 3.0». Соседний `_CMP_RE` сравнивает ключ с ключом; эта форма
#: сравнивает ключ с числом или версией, названными прямо в утверждении.
_LITERAL_CMP_RE = re.compile(
    r"\b([a-z_][a-z0-9_]*|верси\w*)\s+(?:is\s+|записан\w*\s+|)"
    r"(below|under|above|over|less\s+than|greater\s+than|"
    r"ниже|выше|меньше|больше)\s+"
    r"(\d+(?:\.\d+)*)", re.IGNORECASE)

#: Слова, означающие «меньше», для сравнения с литералом.
_LITERAL_LESS_WORDS = {"below", "under", "less than", "ниже", "меньше"}

#: Ключи разности прогона, в порядке предпочтения.
_TOTAL_KEYS = ("tests_total", "total", "tests")
_FAILED_KEYS = ("tests_failed", "failed", "failures")

#: Русское имя ключа `version` — утверждение о версии оператор пишет словом.
_VERSION_ALIASES = {"верси", "version"}


def _version_tuple(raw: str) -> tuple[int, ...] | None:
    """Версия как кортеж компонент, или None если это обычное число.

    Сравнивать версию как float нельзя: `2.11.0` против `2.9.0` даёт
    2.11 < 2.9 — истину там, где правда обратна. Триплет узнаётся по двум и
    более точкам, ровно как в `verifier_absence` («триплет — не число, а имя»).
    """
    if raw.count(".") < 2:
        return None
    try:
        return tuple(int(part) for part in raw.split("."))
    except ValueError:
        return None


def parse_version_pairs(excerpt: str) -> dict[str, tuple[int, ...]]:
    """Пары `key=1.2.3` — версии, которые `parse_pairs` не берёт (не число)."""
    out: dict[str, tuple[int, ...]] = {}
    for line in (excerpt or "").splitlines():
        match = _VERSION_PAIR_RE.match(line)
        if not match:
            continue
        version = _version_tuple(match.group(2))
        if version is not None:
            out[match.group(1).lower()] = version
    return out


_VERSION_PAIR_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*(\d+(?:\.\d+)+)\s*$")


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
    from core.evidence import ENVELOPE_KEYS

    pairs: dict[str, float] = {}
    for line in (excerpt or "").splitlines():
        match = _PAIR_RE.match(line)
        if match:
            key = match.group(1).lower()
            # Конверт прогона — не его данные: см. ENVELOPE_KEYS.
            if key in ENVELOPE_KEYS:
                continue
            pairs[key] = float(match.group(2))
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


def _literal_comparison(
    holds: bool, key: str, shown: str, word: str, literal: str,
    subset: dict[str, float] | None = None,
) -> ClaimVerdict:
    """Вердикт сравнения ключа с названным в утверждении литералом."""
    working = _working(subset) if subset else f"{key}={shown}"
    if holds:
        return ClaimVerdict(
            "supports", code="literal_comparison", expected=word, actual=word,
            computed_from=working,
            explanation=f"{key}={shown} is indeed {word} {literal}")
    return ClaimVerdict(
        "refutes", code="literal_comparison_false",
        expected=f"{key} is NOT {word} {literal}", actual=f"{key}={shown}",
        computed_from=working,
        explanation=f"{key}={shown}, so it is not {word} {literal}")


def evaluate(claim: str, excerpt: str) -> ClaimVerdict:
    """Decide *claim* against *excerpt*, or stay `SILENT`.

    Order matters only where shapes overlap: `_TIMES_RE` is tried before
    `_CMP_RE` because "three times larger" contains a comparison word.
    """
    pairs = parse_pairs(excerpt)
    if not pairs and not parse_version_pairs(excerpt):
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

    match = _LITERAL_CMP_RE.search(text)
    if match:
        key, word, literal = (
            match.group(1).lower(), match.group(2).lower().replace("  ", " "), match.group(3)
        )
        versions = parse_version_pairs(excerpt)
        key = "version" if any(key.startswith(a) for a in _VERSION_ALIASES) else key
        wants_less = word in _LITERAL_LESS_WORDS
        left_v = versions.get(key)
        right_v = _version_tuple(literal)
        if left_v is not None:
            # Версия против литерала: покомпонентно, добивая нулями.
            right = right_v or tuple(int(x) for x in literal.split(".") if x.isdigit())
            width = max(len(left_v), len(right))
            lhs = left_v + (0,) * (width - len(left_v))
            rhs = tuple(right) + (0,) * (width - len(right))
            holds = lhs < rhs if wants_less else lhs > rhs
            shown = ".".join(str(x) for x in left_v)
            return _literal_comparison(holds, key, shown, word, literal)
        if key in pairs:
            claimed_bound = float(literal) if right_v is None else None
            if claimed_bound is not None:
                holds = (pairs[key] < claimed_bound) if wants_less else (pairs[key] > claimed_bound)
                return _literal_comparison(
                    holds, key, _fmt(pairs[key]), word, literal,
                    subset={key: pairs[key]},
                )

    match = _PASSED_RE.search(text)
    if match:
        total_key = next((k for k in _TOTAL_KEYS if k in pairs), None)
        failed_key = next((k for k in _FAILED_KEYS if k in pairs), None)
        if total_key and failed_key:
            claimed_raw = next(g for g in match.groups() if g)
            subset = {total_key: pairs[total_key], failed_key: pairs[failed_key]}
            return _compare(
                code="passed_mismatch",
                expected=pairs[total_key] - pairs[failed_key],
                claimed=_claimed(claimed_raw),
                what=f"{total_key} minus {failed_key}", pairs=pairs, subset=subset,
            )

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
