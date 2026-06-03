"""DLP helpers for sensitive personal data.

Secrets are handled by `core.secret_scanner`. This module covers lower-risk
but still sensitive PII that should not cross durable boundaries raw:
logs, LLM prompts, user output, and persistent memory.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DlpFinding:
    kind: str
    start: int
    end: int
    matched: str


_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Require a leading plus to avoid false positives on versions, IDs, and dates.
_PHONE_RE = re.compile(r"\+\d[\d\s\-()]{7,}\d")

# Russian tax identification number (ИНН):
#   10 digits for legal entities, 12 digits for individuals.
#   Bare digit-runs cannot be the only signal — long unix timestamps,
#   build numbers, hashes and ID values share that shape. We layer two
#   gates on top of the boundary regex:
#     (a) a Russian checksum so a random run of digits has ≤ 10% chance
#         of being accepted (pure entropy on the last digit).
#     (b) a context whitelist that suppresses the finding when the digit
#         run is annotated as a timestamp / id / version / port / hash.
_INN_RE = re.compile(r"\b(?:\d{10}|\d{12})\b")

# Tokens that, when neighbouring the digit run, indicate it is NOT an ИНН
# but a timestamp, identifier, sequence number, or similar non-PII numeric.
# Lowercase, matched as substrings of the surrounding ±32-char window.
_INN_CONTEXT_NEGATIVE_TOKENS: tuple[str, ...] = (
    "unix", "timestamp", "epoch", "ts:", "ts=", "'ts'", '"ts"',
    "millis", "nanos", "elapsed", "duration",
    "build", "version", "revision", "commit", "hash", "sha",
    "port ", "port:", "port=", "pid", "uid", "gid",
    "length", "size", "count", "offset", "bytes",
    "phone", "tel:", "msisdn",
)

# Tokens that strongly signal the digit run IS an ИНН and override the
# context-negative gate (e.g. an explicit "ИНН: ..." label still wins).
_INN_CONTEXT_POSITIVE_TOKENS: tuple[str, ...] = (
    "инн", "inn", "tax id", "налогоплат",
)


def _inn_checksum_valid(digits: str) -> bool:
    """Return True when *digits* satisfies the ФНС ИНН checksum.

    For a 10-digit number the last digit must equal
    ``(Σ d_i · w_i) mod 11 mod 10`` with w = (2,4,10,3,5,9,4,6,8).
    For a 12-digit number two control digits are computed in turn.
    Random digit runs (unix timestamps, hashes) pass with ≤10% probability.
    """
    n = len(digits)
    if n not in (10, 12):
        return False
    d = [int(c) for c in digits]
    if n == 10:
        w = (2, 4, 10, 3, 5, 9, 4, 6, 8)
        return d[9] == (sum(d[i] * w[i] for i in range(9)) % 11) % 10
    w1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
    w2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
    c11 = (sum(d[i] * w1[i] for i in range(10)) % 11) % 10
    c12 = (sum(d[i] * w2[i] for i in range(11)) % 11) % 10
    return d[10] == c11 and d[11] == c12


def _inn_context_excluded(text: str, start: int, end: int) -> bool:
    """Inspect the ±32-char window around the candidate match and decide
    whether the surrounding text marks it as a non-ИНН numeric. Explicit
    "ИНН"/"INN" tokens override the negative tokens."""
    window_start = max(0, start - 32)
    window_end = min(len(text), end + 32)
    window = text[window_start:window_end].lower()
    if any(tok in window for tok in _INN_CONTEXT_POSITIVE_TOKENS):
        return False
    return any(tok in window for tok in _INN_CONTEXT_NEGATIVE_TOKENS)

# Russian pension fund number (СНИЛС): XXX-XXX-XXX XX
_SNILS_RE = re.compile(r"\b\d{3}-\d{3}-\d{3}\s\d{2}\b")

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email",  _EMAIL_RE),
    ("ssn",    _SSN_RE),
    ("phone",  _PHONE_RE),
    ("inn",    _INN_RE),
    ("snils",  _SNILS_RE),
)


def scan_pii(text: str) -> list[DlpFinding]:
    if not isinstance(text, str) or not text:
        return []
    findings: list[DlpFinding] = []
    for kind, pattern in _PII_PATTERNS:
        for match in pattern.finditer(text):
            matched = match.group(0)
            if kind == "inn":
                # Two-stage filter to suppress timestamp/id false positives.
                if not _inn_checksum_valid(matched):
                    continue
                if _inn_context_excluded(text, match.start(), match.end()):
                    continue
            findings.append(
                DlpFinding(
                    kind=kind,
                    start=match.start(),
                    end=match.end(),
                    matched=matched,
                )
            )
    findings.sort(key=lambda item: (item.start, -item.end, item.kind))
    return findings


def pii_markers(text: str) -> list[str]:
    return sorted({finding.kind for finding in scan_pii(text)})


def contains_pii(text: str) -> tuple[bool, list[str]]:
    markers = pii_markers(text)
    if not markers:
        return False, []
    return True, [f"PII markers: {markers}"]


def pii_replacement(kind: str) -> str:
    return f"[REDACTED:pii-{kind}]"
