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

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", _EMAIL_RE),
    ("ssn", _SSN_RE),
    ("phone", _PHONE_RE),
)


def scan_pii(text: str) -> list[DlpFinding]:
    if not isinstance(text, str) or not text:
        return []
    findings: list[DlpFinding] = []
    for kind, pattern in _PII_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                DlpFinding(
                    kind=kind,
                    start=match.start(),
                    end=match.end(),
                    matched=match.group(0),
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
