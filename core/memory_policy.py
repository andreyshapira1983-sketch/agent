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
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable, Literal

from core.dlp import contains_pii
from core.models import MemoryRecord
from core.secret_scanner import contains_secret
# Imported lazily inside `decide` to avoid an import cycle when
# `core/hygiene.py` later wants to reach into models / policies.

if TYPE_CHECKING:
    from core.memory_echo_antibody import MemoryWriteEvent


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

    def __init__(self, frozen_sources: Iterable[str] = ()):
        """`frozen_sources` names write sources that are blocked in this
        context (run-scoped). Used by the operator brake to freeze
        agent-initiated ("agent-auto") memory writes so the agent cannot
        silently grow its own persistent memory without a human in the
        loop. Empty by default — existing behaviour is unchanged. User
        writes (source='user-explicit') are never frozen unless explicitly
        listed.
        """
        self.frozen_sources: frozenset[str] = frozenset(
            (s or "").strip().lower() for s in frozen_sources if s
        )

    def add_frozen_source(self, source: str) -> bool:
        """Freeze ``source`` at runtime (run-scoped operator/audit brake).

        Returns True if the source was newly frozen, False if it was already
        frozen. Callers that later unfreeze can use this so they only lift a
        freeze they themselves installed — e.g. an audit toggle must not
        release the ``AGENT_FREEZE_AUTO_MEMORY`` env brake it did not set.
        """
        key = (source or "").strip().lower()
        if not key or key in self.frozen_sources:
            return False
        self.frozen_sources = self.frozen_sources | {key}
        return True

    def remove_frozen_source(self, source: str) -> bool:
        """Unfreeze ``source`` at runtime. Returns True if it was removed."""
        key = (source or "").strip().lower()
        if not key or key not in self.frozen_sources:
            return False
        self.frozen_sources = self.frozen_sources - {key}
        return True

    def decide(
        self,
        content: str,
        tags: Iterable[str] = (),
        source: str = "agent-auto",
        owner: str = "self",
        existing: Iterable[MemoryRecord] = (),
        recent_writes: "Iterable[MemoryWriteEvent]" = (),
    ) -> MemoryWriteDecision:
        """Decide whether `content` may reach persistent storage.

        `existing` lets the policy refuse near-duplicates of records
        already on disk. Pass `store.load()` from the caller — the
        policy never reads the store itself, keeping it a pure
        function over inputs.

        `recent_writes` is the time-windowed rolling log of recent
        `agent-auto` writes (from `core.memory_echo_antibody`). When
        supplied, the Memory Echo Antibody (A1) refuses an `agent-auto`
        record that merely re-states something the agent already wrote in
        the last window — the "echo chamber" failure mode. `user-explicit`
        writes are never affected.
        """
        reasons: list[str] = []
        tags_set = {t.strip().lower() for t in tags if t}
        text = (content or "").strip()

        # --- context freeze (operator brake) -----------------------------
        # When a write source is frozen for this run, refuse before any
        # content checks. This closes the side channel where the knowledge
        # pipeline auto-persists 'agent-auto' records that PolicyGate never
        # sees (file_write approval does not cover memory writes).
        if (source or "").strip().lower() in self.frozen_sources:
            return MemoryWriteDecision(
                "reject",
                [
                    f"auto-memory writes frozen in this context "
                    f"(source='{source}' needs a human checkpoint)"
                ],
            )

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

        # --- echo gate (A1 Memory Echo Antibody) -------------------------
        # Before the on-disk dedup check, refuse an `agent-auto` write that
        # echoes something the agent itself wrote in the recent window. This
        # catches the "echo chamber" (re-stating the same lesson cycle after
        # cycle) that plain dedup misses because dedup has no clock and no
        # notion of source. `user-explicit` writes pass straight through —
        # the detector no-ops for anything that is not `agent-auto`.
        recent_list = list(recent_writes)
        if recent_list:
            from core.memory_echo_antibody import detect_memory_echo

            echo = detect_memory_echo(
                candidate_content=text,
                candidate_source=source,
                recent_writes=recent_list,
            )
            if echo.is_reject:
                return MemoryWriteDecision("reject", [echo.reason])

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

_BROAD_PROJECT_MEMORY_TOKENS: frozenset[str] = frozenset(
    {
        "bug", "bugs", "project", "memory", "budget",
        "test", "tests", "pytest", "model", "models", "status",
        "usage", "log", "logs",
        "operator-routing", "operator", "routing",
        "patch-proposal", "patch", "proposal",
        "tech debt", "tech", "debt",
        "autonomy", "autonomous",
        "баг", "баги", "ошибка", "ошибки", "проект", "память",
        "бюджет", "тест", "тесты", "модель", "модели", "статус",
        "лог", "логи", "автономность", "автономия",
    }
)

_BROAD_PROJECT_CONTEXT_TERMS: tuple[str, ...] = (
    "your project",
    "this project",
    "our project",
    "my project",
    "current project",
    "project status",
    "project state",
    "project overview",
    "repo",
    "repository",
    "codebase",
    "своем проект",
    "своём проект",
    "твоем проект",
    "твоём проект",
    "нашем проект",
    "этом проект",
    "о проект",
    "про проект",
    "статус проект",
    "состояние проект",
)

_BROAD_PROJECT_QUESTION_TERMS: tuple[str, ...] = (
    "what do you know",
    "already know",
    "tell me about",
    "summarize",
    "summary",
    "overview",
    "status",
    "state",
    "что ты",
    "что уже",
    "знаешь",
    "расскажи",
    "сводка",
    "обзор",
    "статус",
    "состояние",
)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if t.lower() not in _STOPWORDS and len(t) > 1}


def _query_tokens(text: str) -> set[str]:
    tokens = _tokens(text)
    if _is_broad_project_question(text):
        tokens |= _BROAD_PROJECT_MEMORY_TOKENS
    return tokens


def _is_broad_project_question(text: str) -> bool:
    lowered = (text or "").casefold()
    return (
        any(term in lowered for term in _BROAD_PROJECT_CONTEXT_TERMS)
        and any(term in lowered for term in _BROAD_PROJECT_QUESTION_TERMS)
    )


def _tag_tokens(tags: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for tag in tags or ():
        lowered = str(tag).casefold().strip()
        if not lowered:
            continue
        out.add(lowered)
        out.update(_tokens(lowered.replace("-", " ").replace("_", " ")))
    return out


def _record_text(record: MemoryRecord) -> str:
    return record.content if isinstance(record.content, str) else str(record.content)


def _record_haystack(record: MemoryRecord) -> str:
    return " ".join([_record_text(record), " ".join(record.tags or [])]).casefold()


def _is_readme_record(record: MemoryRecord) -> bool:
    haystack = _record_haystack(record)
    return "readme" in haystack or "readme.md" in haystack


def _is_tech_debt_record(record: MemoryRecord) -> bool:
    haystack = _record_haystack(record).replace("_", " ").replace("-", " ")
    return "tech debt" in haystack or "techdebt" in haystack


def _is_live_status_record(record: MemoryRecord) -> bool:
    tokens = _tokens(_record_haystack(record).replace("_", " ").replace("-", " "))
    return bool(tokens & {
        "status", "current", "latest", "recent", "now", "test", "tests",
        "pytest", "passed", "failed", "model", "models", "budget", "usage",
        "log", "logs", "runtime", "scheduler", "сейчас", "статус", "тест",
        "тесты", "модель", "модели", "бюджет", "лог", "логи",
    })


def _is_reference_record(record: MemoryRecord) -> bool:
    tokens = _tokens(_record_haystack(record).replace("_", " ").replace("-", " "))
    return bool(tokens & {
        "architecture", "reference", "overview", "design", "doctrine",
        "архитектура", "архитектур", "справка", "описание",
    })


def _freshness_boost(record: MemoryRecord) -> int:
    created_at = record.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - created_at
    age_seconds = age.total_seconds()
    if age_seconds <= 7 * 24 * 3600:
        return 3
    if age_seconds <= 30 * 24 * 3600:
        return 1
    return 0


def _broad_project_score_adjustment(record: MemoryRecord, base_score: int) -> int:
    score = _freshness_boost(record) if base_score > 0 else 0
    if _is_tech_debt_record(record):
        score += 4
    if _is_live_status_record(record):
        score += 2
    if _is_readme_record(record):
        if _is_live_status_record(record):
            score -= 4
        elif _is_reference_record(record):
            score += 1
    return score


def _record_prompt_note(record: MemoryRecord) -> str:
    if not _is_readme_record(record):
        return ""
    if _is_live_status_record(record):
        return "[historical README/reference; confirm with recent memory/logs before treating as current] "
    return "[README architecture/reference] "


@dataclass(frozen=True)
class RetrievalSelection:
    """What `select` kept, and why each of the rest did not make it.

    The reasons are produced where the decision is taken. An observer that
    re-derives them by subtracting list lengths cannot separate causes it
    never saw: `len(candidates) - len(selected)` reads a `max_records`
    cut-off and a score below the floor as one number, and reports whichever
    label the caller happened to write down. Measured on the live store, that
    inference was wrong for 4 of 6 realistic questions.

    Counts are aggregated **by reason, never per record** — a per-record
    trace would grow with the store and cost more than the retrieval it
    observes. A reason that did not fire is absent, not zero.
    """

    selected: list[MemoryRecord]
    rejected_by: dict[str, int]


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
        """The records to inject. Delegates so there is one decision, not two."""
        return self.select_with_report(records, question).selected

    def select_with_report(
        self,
        records: list[MemoryRecord],
        question: str,
    ) -> RetrievalSelection:
        if not records:
            return RetrievalSelection(selected=[], rejected_by={})
        q_tokens = _query_tokens(question) if question else set()
        if not q_tokens:
            # Nothing was judged about the records at all. Calling this
            # "below threshold" points the reader at the store when the
            # question is what produced no searchable tokens.
            return RetrievalSelection(
                selected=[], rejected_by={"no_query_tokens": len(records)}
            )

        below_threshold = 0
        scored: list[tuple[int, MemoryRecord]] = []
        for r in records:
            text = r.content if isinstance(r.content, str) else str(r.content)
            r_tokens = _tokens(text)
            score = len(q_tokens & r_tokens)
            # Tags also count — weak signal but useful when content is terse.
            tag_tokens = _tag_tokens(r.tags or [])
            score += len(q_tokens & tag_tokens)
            # Prefix match for inflected words (handles Russian morphology):
            # e.g. "уроки" matches tag "урок", "рефлексии" matches "рефлексия".
            if score == 0:
                all_record_tokens = r_tokens | tag_tokens
                for q in q_tokens:
                    if len(q) >= 4:
                        for rt in all_record_tokens:
                            if len(rt) >= 4 and (rt.startswith(q[:4]) or q.startswith(rt[:4])):
                                score += 1
                                break
            if _is_broad_project_question(question):
                score += _broad_project_score_adjustment(r, score)
            if score >= self.min_score:
                scored.append((score, r))
            else:
                below_threshold += 1

        # Higher score first, then newer first.
        scored.sort(key=lambda pair: (pair[0], pair[1].created_at), reverse=True)
        selected = [r for _score, r in scored[: self.max_records]]

        # A cap is not a relevance judgment: these records DID clear the
        # floor and were cut by max_records. Reported separately because the
        # two call for opposite responses — raise the cap vs. write better
        # records.
        rejected_by = {
            k: v for k, v in (
                ("below_threshold", below_threshold),
                ("over_limit", len(scored) - len(selected)),
            ) if v > 0
        }
        return RetrievalSelection(selected=selected, rejected_by=rejected_by)

    def format_for_prompt(self, records: list[MemoryRecord]) -> str:
        if not records:
            return ""
        lines: list[str] = []
        for r in records:
            text = r.content if isinstance(r.content, str) else str(r.content)
            note = _record_prompt_note(r)
            if len(text) > self.per_record_chars:
                text = text[: self.per_record_chars - 1].rstrip() + "…"
            tag_str = ",".join(r.tags) if r.tags else "-"
            lines.append(f"- [{r.id} | tags: {tag_str}] {note}{text}")
        return "\n".join(lines)
