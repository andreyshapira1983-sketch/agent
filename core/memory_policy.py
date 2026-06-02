"""Memory Write Policy + Memory Retrieval Policy (§4 + §12.4).

Two gates around the persistent store:

  MemoryWritePolicy
    Decides BEFORE a record reaches disk. Catches:
      - secrets and credentials (delegated to `core.secret_scanner`)
      - blocked tags (transient / temporary / do-not-save)
      - length extremes (too short / too long)
      - raw tool-result dumps (structured JSON-y noise)
      - records lacking explicit consent (must be user-sourced or
        carry one of the "remember-worthy" tags)
      - third-party data (owner != "self") without explicit cross-owner
        consent — see §7 "Безопасность данных других людей".

  MemoryRetrievalPolicy
    Decides which persistent records get injected into the prompts of
    the current cycle. Keyword overlap scoring, recency tiebreaker,
    capped count + capped per-record length.

Both policies are pure functions over MemoryRecords + question text and are
fully testable on their own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Literal

from core.dlp import contains_pii
from core.models import MemoryRecord
from core.secret_scanner import contains_secret
# Imported lazily inside `decide` to avoid an import cycle when
# `core/hygiene.py` later wants to reach into models / policies.


# ============================================================
# Write Policy
# ============================================================

BLOCKED_TAGS: frozenset[str] = frozenset({"transient", "temporary", "do-not-save", "ephemeral"})

# At least one of these tags (or source="user-explicit") is required.
# Stops the agent from quietly persisting every passing thought.
CONSENT_TAGS: frozenset[str] = frozenset(
    {"preference", "fact", "decision", "insight", "user-approved", "project"}
)

# First-party owners. Anything outside this set is treated as third-party
# data and triggers the cross-owner consent gate below.
FIRST_PARTY_OWNERS: frozenset[str] = frozenset({"self", "user", "session"})

# Cross-owner consent tag: required when owner is third-party. Without it
# the agent is not allowed to persist data belonging to another person.
CROSS_OWNER_CONSENT_TAG = "cross-owner-consent"
SENSITIVE_DATA_CONSENT_TAG = "sensitive-data-consent"

MIN_CONTENT_LEN = 4
MAX_CONTENT_LEN = 4_000

# Heuristic: looks like a raw web_search result dump.
_TOOL_DUMP_HINT = re.compile(r'"url"\s*:\s*"https?://', re.IGNORECASE)


@dataclass
class MemoryWriteDecision:
    decision: Literal["save", "reject"]
    reasons: list[str] = field(default_factory=list)
    policy_id: str = "memory-write-mvp"


class MemoryWritePolicy:
    """Decides whether a candidate MemoryRecord may reach the persistent store."""

    def decide(
        self,
        content: str,
        tags: Iterable[str] = (),
        source: str = "agent-auto",
        owner: str = "self",
        existing: Iterable[MemoryRecord] = (),
    ) -> MemoryWriteDecision:
        """Decide whether `content` may reach persistent storage.

        `existing` lets the policy refuse near-duplicates of records
        already on disk. Pass `store.load()` from the caller — the
        policy never reads the store itself, keeping it a pure
        function over inputs.
        """
        reasons: list[str] = []
        tags_set = {t.strip().lower() for t in tags if t}
        text = (content or "").strip()

        # --- hard blocks (never save, regardless of consent) -------------

        if not text:
            return MemoryWriteDecision("reject", ["empty content"])

        if len(text) < MIN_CONTENT_LEN:
            return MemoryWriteDecision("reject", [f"too short (<{MIN_CONTENT_LEN} chars)"])

        if len(text) > MAX_CONTENT_LEN:
            return MemoryWriteDecision("reject", [f"too long (>{MAX_CONTENT_LEN} chars)"])

        # Secret signals: delegate to the single source of truth so a new
        # pattern added in `secret_scanner.py` is honoured by the policy
        # without code changes here. ALL hits are surfaced so the audit
        # trail records every signal (regex span AND keyword evidence),
        # not just the first one that fired.
        is_secret, secret_reasons = contains_secret(text)
        if is_secret:
            return MemoryWriteDecision("reject", secret_reasons)

        has_pii, pii_reasons = contains_pii(text)
        if has_pii and SENSITIVE_DATA_CONSENT_TAG not in tags_set:
            return MemoryWriteDecision(
                "reject",
                pii_reasons
                + [
                    "sensitive data requires explicit "
                    f"'{SENSITIVE_DATA_CONSENT_TAG}' tag"
                ],
            )

        if _TOOL_DUMP_HINT.search(text):
            return MemoryWriteDecision(
                "reject", ["looks like a raw tool-result dump (url-bearing JSON)"]
            )

        blocked = tags_set & BLOCKED_TAGS
        if blocked:
            return MemoryWriteDecision(
                "reject", [f"carries blocked tag(s): {sorted(blocked)}"]
            )

        # --- consent gate (must be user-sourced OR remember-worthy tag) --

        if source != "user-explicit" and not (tags_set & CONSENT_TAGS):
            return MemoryWriteDecision(
                "reject",
                [
                    "no consent signal: source must be 'user-explicit' or "
                    f"tags must include one of {sorted(CONSENT_TAGS)}"
                ],
            )

        # --- third-party data gate (§7 "данные других людей") ------------
        # When the record belongs to someone outside the first-party set,
        # the only way to persist it is an explicit cross-owner consent
        # tag. Prevents "I learned X about my client; let me just save it"
        # from happening without intent.
        owner_normalised = (owner or "").strip().lower() or "self"
        if owner_normalised not in FIRST_PARTY_OWNERS and CROSS_OWNER_CONSENT_TAG not in tags_set:
            return MemoryWriteDecision(
                "reject",
                [
                    f"owner='{owner}' is third-party and tag "
                    f"'{CROSS_OWNER_CONSENT_TAG}' is missing"
                ],
            )

        # --- dedup gate (§4 Memory Hygiene MVP-10) ------------------------
        # Refuse to persist a near-duplicate of something already on disk.
        # Threshold matches `core.hygiene.DEFAULT_DEDUP_THRESHOLD`. The
        # existing list is supplied by the caller (typically
        # `store.load()`), so this policy stays a pure function.
        existing_list = list(existing)
        if existing_list:
            # Local import keeps memory_policy import-free of hygiene at
            # module load time (and breaks the otherwise-tempting cycle).
            from core.hygiene import find_duplicate, DEFAULT_DEDUP_THRESHOLD

            match = find_duplicate(
                text, existing_list, threshold=DEFAULT_DEDUP_THRESHOLD
            )
            if match is not None:
                rec, score = match
                return MemoryWriteDecision(
                    "reject",
                    [
                        f"duplicate of {rec.id} "
                        f"(similarity={score:.2f} >= {DEFAULT_DEDUP_THRESHOLD})"
                    ],
                )

        reasons.append(f"source={source}")
        reasons.append(f"owner={owner}")
        if tags_set:
            reasons.append(f"tags={sorted(tags_set)}")
        if has_pii:
            reasons.extend(pii_reasons)
            reasons.append("sensitive data will be stored redacted")
        return MemoryWriteDecision("save", reasons)


# ============================================================
# Retrieval Policy
# ============================================================

# Words that add no signal to keyword overlap scoring.
_STOPWORDS: frozenset[str] = frozenset(
    {
        # English
        "a", "an", "the", "and", "or", "but", "is", "are", "was", "were",
        "be", "to", "of", "in", "on", "at", "for", "with", "as", "by",
        "this", "that", "these", "those", "it", "its", "what", "which",
        "who", "whom", "how", "when", "where", "why", "do", "does", "did",
        "i", "you", "he", "she", "we", "they", "me", "us", "them", "my",
        "your", "their", "our", "from", "about",
        # Russian
        "и", "или", "но", "не", "что", "как", "это", "так", "же",
        "в", "на", "с", "по", "из", "у", "к", "о", "за", "от", "до",
        "ли", "бы", "был", "была", "было", "были", "есть", "быть",
        "мой", "моя", "мое", "мои", "твой", "твоя", "ваш", "наш",
    }
)

_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if t.lower() not in _STOPWORDS and len(t) > 1}


class MemoryRetrievalPolicy:
    """Picks the few persistent records most relevant to the current question.

    No embeddings, no vectors — keyword overlap with stopword filtering,
    plus a recency tiebreaker. That is the MVP-5 contract: smart enough to
    surface obvious matches, dumb enough to be deterministic and testable.
    """

    def __init__(
        self,
        max_records: int = 3,
        per_record_chars: int = 400,
        min_score: int = 1,
    ):
        self.max_records = max_records
        self.per_record_chars = per_record_chars
        self.min_score = min_score

    def select(
        self,
        records: list[MemoryRecord],
        question: str,
    ) -> list[MemoryRecord]:
        if not records or not question:
            return []
        q_tokens = _tokens(question)
        if not q_tokens:
            return []

        scored: list[tuple[int, MemoryRecord]] = []
        for r in records:
            text = r.content if isinstance(r.content, str) else str(r.content)
            r_tokens = _tokens(text)
            score = len(q_tokens & r_tokens)
            # Tags also count — weak signal but useful when content is terse.
            tag_tokens = {t.lower() for t in (r.tags or [])}
            score += len(q_tokens & tag_tokens)
            if score >= self.min_score:
                scored.append((score, r))

        # Higher score first, then newer first.
        scored.sort(key=lambda pair: (pair[0], pair[1].created_at), reverse=True)
        return [r for _score, r in scored[: self.max_records]]

    def format_for_prompt(self, records: list[MemoryRecord]) -> str:
        if not records:
            return ""
        lines: list[str] = []
        for r in records:
            text = r.content if isinstance(r.content, str) else str(r.content)
            if len(text) > self.per_record_chars:
                text = text[: self.per_record_chars - 1].rstrip() + "…"
            tag_str = ",".join(r.tags) if r.tags else "-"
            lines.append(f"- [{r.id} | tags: {tag_str}] {text}")
        return "\n".join(lines)
