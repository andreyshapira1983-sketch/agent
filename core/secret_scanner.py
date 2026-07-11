"""Secret Scanner — single source of truth for credential detection (§7).

The architecture rule is: security belongs to the kernel, not to the LLM.
This module is that kernel. Every other component that needs to know
"is there a secret in this text?" must ask this module — not roll its own
patterns.

Used by:
  - MemoryWritePolicy           refuses to persist any record containing a hit
  - redaction.redact_text       masks hits inline before logs/prompts/output
  - DataClassifier              elevates classification to DataClass.SECRET
  - AgentLoop                   classifies tool outputs before they leave the loop

Two detection layers, intentionally separate:

  REGEX_RULES
    Specific, high-confidence credential shapes (OpenAI keys, GitHub PATs,
    AWS access keys, PEM blocks, `KEY=VALUE` style assignments, …).
    These have precise spans — they can be redacted inline.

  KEYWORD_RULES
    Soft alarms. The text *talks about* credentials (e.g. "password", an
    `Authorization:` header). The exact span of the secret value is
    ambiguous, so these mark the whole document as "contains secret",
    refuse memory writes, and bump classification — but do NOT drive
    inline redaction.

Adding a new pattern? Do it here, once. Every consumer picks it up for free.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final


# Regex rules: (kind, compiled pattern). `kind` ends up in the redaction
# token, e.g. `[REDACTED:openai-key]`, so keep it short, lowercase, kebab.
REGEX_RULES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("anthropic-key",      re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-key",         re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("github-pat",         re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("huggingface-token",  re.compile(r"hf_[A-Za-z0-9]{20,}")),
    ("aws-access-key",     re.compile(r"AKIA[0-9A-Z]{16}")),
    ("bearer-token",       re.compile(r"Bearer\s+[A-Za-z0-9_.\-]{20,}")),
    # PEM block start marker alone is enough — pasting only the header is
    # already a leak. The END marker is optional in match.
    ("private-key-block",  re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")),
    # JWT: three base64url-encoded segments separated by dots.
    # The first segment starts with eyJ (base64 of '{"') which is distinctive
    # enough to avoid false positives on version strings or file paths.
    ("jwt-token",          re.compile(
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    )),
    # Telegram bot token: <bot_id>:<token>. Standard format issued by BotFather.
    ("telegram-bot-token", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b")),
    # MongoDB/Atlas connection strings that embed a password.
    ("mongodb-uri",        re.compile(
        r"mongodb(?:\+srv)?://[^:/?#\s]+:[^@/?#\s]+@"
    )),
    # `KEY=VALUE` / `KEY: VALUE` shapes where KEY names a credential.
    # Matches the WHOLE assignment so the redactor can mask the value.
    ("credential-assignment", re.compile(
        r"(?i)\b(api[_-]?key|apikey|secret[_-]?key|password|passwd|passphrase|"
        r"auth[_-]?token|private[_-]?key|access[_-]?token)\s*[:=]\s*\S+"
    )),
)


# Keyword rules: case-insensitive substring matches. These don't pinpoint a
# span — they flag the whole text as credential-adjacent. Order doesn't matter.
KEYWORD_RULES: Final[tuple[str, ...]] = (
    "api_key", "api-key", "apikey",
    "password", "passwd", "passphrase",
    "secret_key", "private_key",
    "authorization:", "auth_token",
)


@dataclass(frozen=True)
class SecretFinding:
    """A single regex hit inside a piece of text.

    `matched` holds the raw bytes that triggered the match. Callers MUST
    NOT log it — the whole point of this module is that the raw value
    stays inside the kernel. The kernel passes findings to the redactor,
    which replaces the span with a `[REDACTED:<kind>]` token.
    """

    kind: str
    start: int
    end: int
    matched: str


def scan(text: str) -> list[SecretFinding]:
    """Return every regex hit in `text`. Empty list for clean input.

    Hits across different rules CAN overlap (e.g. an Anthropic key is also
    a substring of an OpenAI-shape match). The redactor handles overlap by
    sorting hits and skipping covered ranges.
    """
    if not text:
        return []
    findings: list[SecretFinding] = []
    for kind, pat in REGEX_RULES:
        for m in pat.finditer(text):
            findings.append(SecretFinding(kind=kind, start=m.start(), end=m.end(), matched=m.group(0)))
    return findings


def keyword_hits(text: str) -> list[str]:
    """Return the keywords found in `text` (lowercased).

    Used by MemoryWritePolicy to refuse a write even when no regex span
    matches: a document that mentions "password=…" loud enough to use the
    word "password" is one we treat as credential-adjacent.
    """
    if not text:
        return []
    lower = text.lower()
    return [kw for kw in KEYWORD_RULES if kw in lower]


def contains_secret(text: str, *, include_keywords: bool = True) -> tuple[bool, list[str]]:
    """High-level check: does this text contain ANY secret signal?

    Returns (flag, reasons). Reasons follow the format the existing
    MemoryWritePolicy reasons used so existing audit consumers keep working:
      - "matches secret pattern '<kind>'"
      - "contains secret keyword '<kw>'"

    ``include_keywords`` controls the soft KEYWORD layer. When False, only
    the high-confidence REGEX rules (real credential *shapes* with a
    redactable span) count. Callers that scan content originating inside
    the trusted boundary — e.g. the agent's own logs, files and diffs —
    pass False so that merely *mentioning* a credential word (``api_key``,
    ``password``) does not mark their own evidence as a secret. Regex hits
    are never suppressed, so real leaked keys are still caught.
    """
    reasons: list[str] = []
    for f in scan(text):
        reasons.append(f"matches secret pattern '{f.kind}'")
    if include_keywords:
        for kw in keyword_hits(text):
            reasons.append(f"contains secret keyword '{kw}'")
    return bool(reasons), reasons
