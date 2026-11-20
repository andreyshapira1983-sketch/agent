"""DLP helpers for sensitive personal data."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DlpFinding:
    """A single regex hit (secrets, personal data).

    `matched` holds the raw value and must never be logged; the redactor replaces the span with [REDACTED:<kind>].
    core.secret_scanner.SecretFinding is this class under its old name.
    """

    kind: str
    start: int
    end: int
    matched: str


_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Require a leading plus to avoid false positives on versions, IDs, and dates.
_PHONE_RE = re.compile(r"\+\d[\d\s\-()]{7,}\d")

# Russian tax number (ИНН), 10 or 12 digits. Bare digit runs are no signal, so two gates apply:
# a checksum, and a context whitelist that suppresses timestamp/id/version/port/hash annotations.
_INN_RE = re.compile(r"\b(?:\d{10}|\d{12})\b")

# Tokens near the digit run meaning "not an ИНН" (timestamp, id, sequence number); lowercase, substring match in a ±32-char window.
_INN_CONTEXT_NEGATIVE_TOKENS: tuple[str, ...] = (
    "unix", "timestamp", "epoch", "ts:", "ts=", "'ts'", '"ts"',
    "millis", "nanos", "elapsed", "duration",
    "build", "version", "revision", "commit", "hash", "sha",
    "port ", "port:", "port=", "pid", "uid", "gid",
    "length", "size", "count", "offset", "bytes",
    "phone", "tel:", "msisdn",
)

# Tokens meaning the run IS an ИНН; they override the negative gate.
_INN_CONTEXT_POSITIVE_TOKENS: tuple[str, ...] = (
    "инн", "inn", "tax id", "налогоплат",
)


def _inn_checksum_valid(digits: str) -> bool:
    """True when digits satisfy the ФНС ИНН checksum (10 digits: last digit; 12 digits: two control digits)."""
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
    """True when the ±32-char window marks the match as a non-ИНН numeric; explicit ИНН/INN tokens override."""
    window_start = max(0, start - 32)
    window_end = min(len(text), end + 32)
    window = text[window_start:window_end].lower()
    if any(tok in window for tok in _INN_CONTEXT_POSITIVE_TOKENS):
        return False
    return any(tok in window for tok in _INN_CONTEXT_NEGATIVE_TOKENS)

#: An ISO date inside a "phone" means it is not a phone (labels like "+29 (2026-08-31)" would otherwise match _PHONE_RE).
_DATE_INSIDE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _looks_like_a_dated_note(matched: str) -> bool:
    """True when the "phone" contains a calendar date."""
    return bool(_DATE_INSIDE_RE.search(matched))


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
            if kind == "phone" and _looks_like_a_dated_note(matched):
                # Dated note, not a number (see _DATE_INSIDE_RE).
                continue
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
