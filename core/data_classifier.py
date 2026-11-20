"""Classify text into public / private / sensitive / secret data classes with reasons."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from core.dlp import pii_markers
from core.secret_scanner import contains_secret


class DataClass(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    SENSITIVE = "sensitive"
    # A data-class label, not a secret itself; keep this comment from starting with a directive word.
    SECRET = "secret"  # noqa: S105


# Source hints carry provenance; they set the default class when no secret/PII evidence is found.
SourceHint = Literal["file", "web", "cli", "memory", "tool_output", "unknown"]


@dataclass(frozen=True)
class ClassificationResult:
    cls: DataClass
    reasons: list[str]
    source: SourceHint


_SOURCE_DEFAULTS: dict[SourceHint, DataClass] = {
    "web": DataClass.PUBLIC,
    "file": DataClass.PRIVATE,
    "cli": DataClass.PRIVATE,
    "memory": DataClass.PRIVATE,
    "tool_output": DataClass.PRIVATE,
    "unknown": DataClass.PRIVATE,
}


def classify(
    text: str,
    source: SourceHint = "unknown",
    *,
    keyword_secrets: bool = True,
) -> ClassificationResult:
    """Assign a DataClass to text with reasons: secret signals -> SECRET, any PII -> SENSITIVE, else the per-source default.

    keyword_secrets=False for output from inside the trusted boundary: a bare credential word then does not elevate it;
    real credential shapes still classify as SECRET.
    """
    reasons: list[str] = []

    if not isinstance(text, str) or not text:
        return ClassificationResult(
            cls=_SOURCE_DEFAULTS[source],
            reasons=["empty content; falling back to source default"],
            source=source,
        )

    is_secret, secret_reasons = contains_secret(text, include_keywords=keyword_secrets)
    if is_secret:
        return ClassificationResult(
            cls=DataClass.SECRET,
            reasons=secret_reasons,
            source=source,
        )

    pii = pii_markers(text)
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
