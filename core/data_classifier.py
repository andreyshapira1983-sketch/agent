"""Data Classifier (§7 Data Governance).

Assigns one of four DataClasses to a piece of text so the agent can apply
the right governance policy:

  public      Free to use, log, save, send to LLM.
              Default for `web_search` results.
  private     Use for the current task, may save with consent.
              Default for `file_read` outputs and user input.
  sensitive   Personal data (email, SSN, …). Must not be saved
              without an explicit user-approved tag, redact when shown.
  secret      Credentials. Must not be saved, must not appear in any
              prompt or output. Always redacted by the kernel.

The classifier is heuristic and intentionally conservative: when it sees
both PII and secret markers, it returns `secret` (the strictest class).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from core.secret_scanner import contains_secret


class DataClass(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    SENSITIVE = "sensitive"
    SECRET = "secret"


# Source hints carry the provenance of the text. They decide the default
# class when no stronger evidence (secret / PII) is found.
SourceHint = Literal["file", "web", "cli", "memory", "tool_output", "unknown"]


# PII heuristics. Tight enough to avoid false positives on technical text.
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# International phone numbers tend to false-positive on version strings /
# IDs, so we require a leading `+` for phone detection.
_PHONE_RE = re.compile(r"\+\d[\d\s\-()]{7,}\d")


def _has_pii(text: str) -> list[str]:
    hits: list[str] = []
    if _EMAIL_RE.search(text):
        hits.append("email")
    if _SSN_RE.search(text):
        hits.append("ssn")
    if _PHONE_RE.search(text):
        hits.append("phone")
    return hits


@dataclass(frozen=True)
class ClassificationResult:
    cls: DataClass
    reasons: list[str]
    source: SourceHint


# Per-source default class when no stronger signal is found.
_SOURCE_DEFAULTS: dict[SourceHint, DataClass] = {
    "web": DataClass.PUBLIC,
    "file": DataClass.PRIVATE,
    "cli": DataClass.PRIVATE,
    "memory": DataClass.PRIVATE,
    "tool_output": DataClass.PRIVATE,
    "unknown": DataClass.PRIVATE,
}


def classify(text: str, source: SourceHint = "unknown") -> ClassificationResult:
    """Assign a DataClass to `text` with explanatory reasons.

    Decision rules, in order:
      1. Secret signals (regex or keyword) -> SECRET
      2. Any PII match  -> SENSITIVE
      3. Otherwise the per-source default (web=public, everything else=private)
    """
    reasons: list[str] = []

    if not isinstance(text, str) or not text:
        return ClassificationResult(
            cls=_SOURCE_DEFAULTS[source],
            reasons=["empty content; falling back to source default"],
            source=source,
        )

    is_secret, secret_reasons = contains_secret(text)
    if is_secret:
        return ClassificationResult(
            cls=DataClass.SECRET,
            reasons=secret_reasons,
            source=source,
        )

    pii = _has_pii(text)
    if pii:
        reasons.append(f"PII markers: {pii}")
        return ClassificationResult(
            cls=DataClass.SENSITIVE,
            reasons=reasons,
            source=source,
        )

    default = _SOURCE_DEFAULTS[source]
    reasons.append(f"no secret / PII markers; source='{source}' default")
    return ClassificationResult(cls=default, reasons=reasons, source=source)
