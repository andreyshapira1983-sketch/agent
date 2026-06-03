"""Structured fact extraction for tool outputs.

Tools often return structured values — dicts, lists, JSON — that the
LLM then verbalises in prose. The Verifier's substring matcher cannot
bridge the gap: ``{"weekday": "Wednesday", "month": 6}`` does not
contain the literal string ``"среда, июнь"``, but a Russian-language
answer paraphrasing the same fact should still be considered grounded.

This module normalises both sides into a small comparable fact set:
calendar dates, weekdays (en + ru), meaningful numbers, booleans and
verbatim strings. The Verifier uses :func:`claim_supported_by` to
decide whether a claim is consistent with the structured source.

Public API:
    extract_facts(excerpt) -> StructuredFacts
    claim_supported_by(claim, facts) -> bool

The extractor is purely textual and never calls an LLM. It accepts
JSON, Python ``repr`` of a dict, or any nested combination thereof.
Anything it cannot parse yields an empty :class:`StructuredFacts` and
the Verifier falls back to its existing matchers.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Locale tables — kept small on purpose; we cover the calendar surface
# the agent's tools actually emit (en + ru). Adding a locale = appending
# tuples here, no other code changes needed.
# ---------------------------------------------------------------------------

_WEEKDAY_NAMES: dict[int, tuple[str, ...]] = {
    0: ("monday", "понедельник", "пн", "mon"),
    1: ("tuesday", "вторник", "вт", "tue"),
    2: ("wednesday", "среда", "ср", "wed"),
    3: ("thursday", "четверг", "чт", "thu"),
    4: ("friday", "пятница", "пт", "fri"),
    5: ("saturday", "суббота", "сб", "sat"),
    6: ("sunday", "воскресенье", "вс", "sun"),
}
_WEEKDAY_TO_INDEX: dict[str, int] = {
    name.lower(): idx
    for idx, names in _WEEKDAY_NAMES.items()
    for name in names
}

_MONTH_NAMES: dict[int, tuple[str, ...]] = {
    1:  ("january",   "январь",   "января",   "янв", "jan"),
    2:  ("february",  "февраль",  "февраля",  "фев", "feb"),
    3:  ("march",     "март",     "марта",    "мар", "mar"),
    4:  ("april",     "апрель",   "апреля",   "апр", "apr"),
    5:  ("may",       "май",      "мая"),
    6:  ("june",      "июнь",     "июня",     "июн", "jun"),
    7:  ("july",      "июль",     "июля",     "июл", "jul"),
    8:  ("august",    "август",   "августа",  "авг", "aug"),
    9:  ("september", "сентябрь", "сентября", "сен", "sep"),
    10: ("october",   "октябрь",  "октября",  "окт", "oct"),
    11: ("november",  "ноябрь",   "ноября",   "ноя", "nov"),
    12: ("december",  "декабрь",  "декабря",  "дек", "dec"),
}

_BOOL_TRUE_FORMS:  frozenset[str] = frozenset({
    "true", "yes", "ok", "success", "passed", "да",
})
_BOOL_FALSE_FORMS: frozenset[str] = frozenset({
    "false", "no", "fail", "failed", "error", "нет",
})

# Numbers shorter than 3 chars (0,1,2,…,99) match almost any prose
# accidentally. Require ≥3 digits for numeric matches to count.
_MIN_NUMERIC_LEN = 3

# Generic strings that appear in every tool output and carry no
# identity. Filter them so a verbatim string fact must be meaningful.
_STRING_STOPWORDS: frozenset[str] = frozenset({
    "true", "false", "none", "null", "ok", "success", "error", "yes", "no",
})

_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StructuredFacts:
    """Normalised, locale-aware view of a structured tool output.

    Each field is a frozenset of lower-case strings. Membership in any
    field is a positive signal that the claim is consistent with the
    source — no field implies a NEGATIVE signal (we do not assert
    contradiction from absence).
    """
    dates:    frozenset[str]
    weekdays: frozenset[str]
    numbers:  frozenset[str]
    booleans: frozenset[str]
    strings:  frozenset[str]

    def is_empty(self) -> bool:
        return not (
            self.dates or self.weekdays or self.numbers
            or self.booleans or self.strings
        )

    @classmethod
    def empty(cls) -> "StructuredFacts":
        return cls(
            dates=frozenset(),
            weekdays=frozenset(),
            numbers=frozenset(),
            booleans=frozenset(),
            strings=frozenset(),
        )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_excerpt(excerpt: str) -> Any:
    """Best-effort parse of a tool output excerpt.

    Tries JSON first (some tools serialise structured outputs that way),
    then ``ast.literal_eval`` for Python ``repr`` of dict/list/tuple.
    Returns ``None`` on any failure — the Verifier falls back to its
    string matchers.
    """
    s = (excerpt or "").strip()
    if not s:
        return None
    if s[0] in "{[":
        try:
            return json.loads(s)
        except (ValueError, TypeError):
            pass
    try:
        return ast.literal_eval(s)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None


def _flatten(value: Any) -> Iterable[tuple[str | None, Any]]:
    """Yield (key, value) pairs from a nested structure. Top-level
    list / tuple items emit ``key=None``."""
    if isinstance(value, dict):
        for k, v in value.items():
            key = k if isinstance(k, str) else None
            yield key, v
            yield from _flatten(v)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten(item)


def _add_date_forms(year: int, month: int, day: int, out: set[str]) -> None:
    """Emit every locale spelling of (year, month, day) we want to
    accept as a positive match in the claim text."""
    out.add(f"{year:04d}-{month:02d}-{day:02d}")
    out.add(f"{day:02d}.{month:02d}.{year:04d}")
    out.add(f"{day}/{month}/{year}")
    for name in _MONTH_NAMES.get(month, ()):
        out.add(f"{day} {name} {year}")
        out.add(f"{day} {name}")
        out.add(f"{day:02d} {name}")
        out.add(f"{name} {day}, {year}")


def extract_facts(excerpt: str | Any) -> StructuredFacts:
    """Parse *excerpt* and produce normalised facts.

    Accepts either a string (JSON or repr) or an already-parsed
    Python value. Returns ``StructuredFacts.empty()`` when the excerpt
    is unparseable or carries no recognisable fields.
    """
    if isinstance(excerpt, str):
        parsed = _parse_excerpt(excerpt)
    else:
        parsed = excerpt
    if parsed is None or not isinstance(parsed, (dict, list, tuple)):
        return StructuredFacts.empty()

    dates:    set[str] = set()
    weekdays: set[str] = set()
    numbers:  set[str] = set()
    booleans: set[str] = set()
    strings:  set[str] = set()

    year: int | None = None
    month: int | None = None
    day: int | None = None

    for key, val in _flatten(parsed):
        kl = (key or "").lower()
        # Booleans MUST be checked before int — Python's True/False are int.
        if isinstance(val, bool):
            booleans.add("true" if val else "false")
            continue
        if isinstance(val, (int, float)):
            n = ("%g" % val) if isinstance(val, float) else str(val)
            if len(n.lstrip("-")) >= _MIN_NUMERIC_LEN:
                numbers.add(n)
            if isinstance(val, int):
                if kl == "year" and 1900 <= val <= 2100:
                    year = val
                elif kl == "month" and 1 <= val <= 12:
                    month = val
                elif kl == "day" and 1 <= val <= 31:
                    day = val
                elif kl in ("weekday_index", "wday") and 0 <= val <= 6:
                    weekdays.update(_WEEKDAY_NAMES[val])
            continue
        if isinstance(val, str):
            sv = val.strip()
            if not sv:
                continue
            sv_lower = sv.lower()
            if sv_lower in _BOOL_TRUE_FORMS:
                booleans.add("true")
            elif sv_lower in _BOOL_FALSE_FORMS:
                booleans.add("false")
            else:
                if (
                    len(sv) >= 3
                    and sv_lower not in _STRING_STOPWORDS
                ):
                    strings.add(sv_lower)
                if sv_lower in _WEEKDAY_TO_INDEX:
                    idx = _WEEKDAY_TO_INDEX[sv_lower]
                    weekdays.update(_WEEKDAY_NAMES[idx])
                for m in _ISO_DATE_RE.finditer(sv):
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    if 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                        _add_date_forms(y, mo, d, dates)
            continue

    if year and month and day:
        _add_date_forms(year, month, day, dates)

    return StructuredFacts(
        dates=frozenset(d.lower() for d in dates),
        weekdays=frozenset(w.lower() for w in weekdays),
        numbers=frozenset(numbers),
        booleans=frozenset(booleans),
        strings=frozenset(strings),
    )


# ---------------------------------------------------------------------------
# Claim matching
# ---------------------------------------------------------------------------

def _word_in(needle: str, hay: str) -> bool:
    """Substring match with word-boundary on Unicode letter/digit edges.
    Cheaper than re.search and good enough for our token shapes."""
    if not needle:
        return False
    idx = hay.find(needle)
    while idx != -1:
        before = hay[idx - 1] if idx > 0 else " "
        after_idx = idx + len(needle)
        after = hay[after_idx] if after_idx < len(hay) else " "
        if not (before.isalnum() or before == "_") and not (
            after.isalnum() or after == "_"
        ):
            return True
        idx = hay.find(needle, idx + 1)
    return False


def claim_supported_by(claim: str, facts: StructuredFacts) -> bool:
    """Return True iff *claim* mentions at least one non-generic fact.

    Matching is case-insensitive and word-boundary-aware so that a
    short fact like ``"6"`` does not match ``"6th"`` accidentally,
    and the weekday ``"ср"`` does not match the inside of ``"среда"``
    from a different source.
    """
    if facts.is_empty():
        return False
    text = (claim or "").lower()
    if not text:
        return False

    for s in facts.dates:
        if s in text:
            return True
    for s in facts.weekdays:
        if _word_in(s, text):
            return True
    for n in facts.numbers:
        if _word_in(n, text):
            return True
    for s in facts.booleans:
        if _word_in(s, text):
            return True
    for s in facts.strings:
        if len(s) >= 3 and _word_in(s, text):
            return True
    return False
