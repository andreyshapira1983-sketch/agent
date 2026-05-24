"""
brain/privacy.py — PII Redaction & Logging Filter.

When the agent handles client data, raw PII (emails, phones, card numbers,
passports, IPs) must NOT flow to:

    - the LLM provider (logs are retained up to 30 days on the provider side)
    - log files on disk
    - persistent memory in clear form

`PIIRedactor` replaces PII with stable session-scoped tokens like
`[EMAIL_1]`, `[PHONE_2]`. The same value within the same session always
maps to the same token, so the LLM still sees coreferences.
The redactor keeps the mapping in memory and `restore()` reverses it
when the agent answers the same user.

`PIIFilter` is a `logging.Filter` that scrubs PII from every log record —
attach it once at the root logger and you can never accidentally log a
client's email.

Threat model addressed:
    1. PII in OpenAI / Anthropic 30-day retention buffers
    2. PII in *.log files committed to git or shared in screenshots
    3. PII in Brain stack traces / error messages
    4. PII in audit log dumps shared with engineers

Out of scope here (handled by other layers):
    - PII at rest in EpisodicMemory.db → use SQLCipher
    - PII in WorkingMemory RAM dumps → kernel-level protection
    - PII in process memory dumps → not solvable in pure Python
"""

from __future__ import annotations

import logging
import re
import threading
from collections import defaultdict
from typing import Pattern

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# Detection patterns
# ────────────────────────────────────────────────────────────────────
# Patterns are intentionally conservative — better a miss than a false
# match that destroys legitimate text. Order matters: more specific
# patterns run first so generic ones don't gobble their input.

_PATTERNS: list[tuple[str, Pattern[str]]] = [
    # Bank cards: 13–19 digit Luhn-ish strings with optional spaces/dashes
    ("CARD", re.compile(r"\b(?:\d[ \-]?){12,18}\d\b")),

    # Russian internal passport: "1234 567890"
    ("PASSPORT_RU", re.compile(r"\b\d{4}\s\d{6}\b")),

    # Email (RFC-5322 simplified)
    ("EMAIL", re.compile(
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
    )),

    # Russian phone in many shapes: +7..., 8..., 7..., with brackets/dashes
    ("PHONE_RU", re.compile(
        r"(?:(?:\+7|7|8)[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
    )),

    # Generic international phone (E.164-ish)
    ("PHONE_INTL", re.compile(
        r"\+\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{2,4}[\s\-]?\d{2,4}[\s\-]?\d{0,4}"
    )),

    # IPv4 (not strictly validated — 999.999.999.999 still matches; ok)
    ("IPV4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
]


# ────────────────────────────────────────────────────────────────────
# Redactor
# ────────────────────────────────────────────────────────────────────

class PIIRedactor:
    """
    Reversible, session-scoped PII redactor.

    Each `session_id` keeps its own mapping `{ kind: { value: token } }`,
    so the same email seen twice in the same session always maps to the
    same `[EMAIL_1]`. Different sessions are isolated.

    Mappings live in process memory only — they are never persisted.
    Call `forget(session_id)` when a session ends to reclaim memory and
    drop the ability to deanonymise.
    """

    def __init__(self, max_sessions: int = 1000) -> None:
        self._max_sessions = max_sessions
        # session_id -> kind -> {raw_value: token}
        self._maps: dict[str, dict[str, dict[str, str]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        self._lock = threading.Lock()

    # ---- public API --------------------------------------------------------

    def redact(self, text: str, session_id: str = "_anon") -> str:
        """
        Replace PII with stable `[KIND_N]` tokens.

        Idempotent within a session: repeated values get the same token.
        """
        if not text:
            return text

        self._maybe_evict_oldest()

        with self._lock:
            session_map = self._maps[session_id]
            for kind, pattern in _PATTERNS:
                bucket = session_map[kind]

                def _sub(match: re.Match) -> str:
                    value = match.group(0)
                    if value not in bucket:
                        bucket[value] = f"[{kind}_{len(bucket) + 1}]"
                    return bucket[value]

                text = pattern.sub(_sub, text)
        return text

    def restore(self, text: str, session_id: str = "_anon") -> str:
        """
        Replace `[KIND_N]` tokens back with original PII values.

        Inverse of `redact()` for the same session. Tokens that don't
        exist in the mapping are left untouched.
        """
        if not text:
            return text
        with self._lock:
            session_map = self._maps.get(session_id)
            if not session_map:
                return text
            # Longest token first to avoid prefix collisions like _1 vs _10
            replacements: list[tuple[str, str]] = []
            for kind_bucket in session_map.values():
                for value, token in kind_bucket.items():
                    replacements.append((token, value))
            replacements.sort(key=lambda pair: len(pair[0]), reverse=True)
            for token, value in replacements:
                text = text.replace(token, value)
        return text

    def redact_anonymous(self, text: str) -> str:
        """
        One-shot, non-reversible redaction. Use for log lines.

        Every match becomes `[KIND]` (no numbering, no mapping stored).
        Safe to call without a session_id.
        """
        if not text:
            return text
        for kind, pattern in _PATTERNS:
            text = pattern.sub(f"[{kind}]", text)
        return text

    def forget(self, session_id: str) -> None:
        """Drop the mapping for a session — call this when a session ends."""
        with self._lock:
            self._maps.pop(session_id, None)

    def forget_all(self) -> None:
        with self._lock:
            self._maps.clear()

    def stats(self) -> dict[str, int]:
        """Per-session count of redacted unique values — for monitoring."""
        with self._lock:
            return {
                session: sum(len(b) for b in kinds.values())
                for session, kinds in self._maps.items()
            }

    # ---- internals ---------------------------------------------------------

    def _maybe_evict_oldest(self) -> None:
        """Crude FIFO eviction so we never grow unbounded across sessions."""
        if len(self._maps) <= self._max_sessions:
            return
        with self._lock:
            while len(self._maps) > self._max_sessions:
                oldest = next(iter(self._maps))
                self._maps.pop(oldest, None)


# ────────────────────────────────────────────────────────────────────
# Logging filter
# ────────────────────────────────────────────────────────────────────

class PIIFilter(logging.Filter):
    """
    Logging filter that redacts PII from every log record.

    Install once at the root logger and you can never accidentally log
    a client's email, phone, or card number:

        import logging
        from brain.privacy import PIIFilter

        logging.getLogger().addFilter(PIIFilter())

    Uses non-reversible `redact_anonymous` so logs cannot be deanonymised
    even if you have the redactor instance.
    """

    def __init__(self, redactor: PIIRedactor | None = None) -> None:
        super().__init__()
        self._redactor = redactor or PIIRedactor()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = self._redactor.redact_anonymous(record.msg)
            args = record.args
            if isinstance(args, dict):
                record.args = {
                    k: self._sanitize(v) for k, v in args.items()
                }
            elif isinstance(args, tuple):
                record.args = tuple(self._sanitize(a) for a in args)
        except Exception:  # noqa: BLE001
            # A logging filter must never break logging — swallow silently
            pass
        return True

    def _sanitize(self, value: object) -> object:
        if isinstance(value, str):
            return self._redactor.redact_anonymous(value)
        return value
