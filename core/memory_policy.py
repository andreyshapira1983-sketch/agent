"""Memory write and retrieval policies: what may be persisted and what is recalled into a prompt.

Both are pure functions over MemoryRecords + question text; the caller supplies the store contents.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from core.bilingual_terms import english_terms_for
from core.dlp import contains_pii
from core.doc_routing import is_broad_project_self_knowledge_question
from core.models import MemoryRecord
from core.secret_scanner import contains_secret
from core.topic_tokens import STOPWORDS as _TOPIC_STOPWORDS
from core.work_kinds import work_kinds

# Hygiene/echo modules are imported lazily inside `decide` to avoid an import cycle.

if TYPE_CHECKING:
    from core.memory_echo_antibody import MemoryWriteEvent


# ============================================================
# Write Policy
# ============================================================

BLOCKED_TAGS: frozenset[str] = frozenset({"transient", "temporary", "do-not-save", "ephemeral"})

# One of these tags (or source="user-explicit") is required, so the agent
# does not quietly persist every passing thought.
CONSENT_TAGS: frozenset[str] = frozenset(
    {"preference", "fact", "decision", "insight", "user-approved", "project"}
)

# Any other owner is third-party and needs CROSS_OWNER_CONSENT_TAG to be saved.
FIRST_PARTY_OWNERS: frozenset[str] = frozenset({"self", "user", "session"})

CROSS_OWNER_CONSENT_TAG = "cross-owner-consent"
SENSITIVE_DATA_CONSENT_TAG = "sensitive-data-consent"

MIN_CONTENT_LEN = 4
MAX_CONTENT_LEN = 4_000

# Heuristic: looks like a raw web_search result dump.
_TOOL_DUMP_HINT = re.compile(r'"url"\s*:\s*"https?://', re.IGNORECASE)


#: Строка родословной записи: «Источник: URL (прочитан …)» / «Источники: file:…».
_PROVENANCE_LINE = re.compile(r"^(?:Источник|Источники):[^\n]*$", re.MULTILINE)


def cut_keeping_provenance(text: str, limit: int) -> str:
    """Укоротить запись до `limit`, сохранив её последнюю строку «Источник: …».

    Строка источника стоит в конце записи, и простой срез отрезал её; поэтому
    укорачивается середина.
    """
    found = list(_PROVENANCE_LINE.finditer(text))
    tail = found[-1].group(0)[:200] if found else ""
    if not tail or len(tail) >= limit // 2:
        return text[: limit - 1].rstrip() + "…"
    # Вопрос уступает первым: вывод и источник важнее его полного текста.
    lines = text[: found[-1].start()].rstrip().splitlines()
    lines = [ln[:90].rstrip() + "…" if ln.startswith("Вопрос:") and len(ln) > 91 else ln for ln in lines]
    head = "\n".join(lines)
    if len(head) + len(tail) + 1 <= limit:
        return head + "\n" + tail
    return head[: limit - len(tail) - 2].rstrip() + "…\n" + tail


@dataclass
class MemoryWriteDecision:
    decision: Literal["save", "reject"]
    reasons: list[str] = field(default_factory=list)
    policy_id: str = "memory-write-mvp"


class MemoryWritePolicy:
    """Decides whether a candidate MemoryRecord may reach the persistent store."""

    def __init__(self, frozen_sources: Iterable[str] = ()):
        """`frozen_sources`: write sources blocked for this run (operator brake on "agent-auto")."""
        self.frozen_sources: frozenset[str] = frozenset(
            (s or "").strip().lower() for s in frozen_sources if s
        )

    def add_frozen_source(self, source: str) -> bool:
        """Freeze ``source`` at runtime; True only if newly frozen.

        The return value lets a caller lift only a freeze it installed itself
        (e.g. not the ``AGENT_FREEZE_AUTO_MEMORY`` env brake).
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

    def decide(  # noqa: PLR0911 — flat: depth 2, all 13 returns are guard clauses
        self,
        content: str,
        tags: Iterable[str] = (),
        source: str = "agent-auto",
        owner: str = "self",
        existing: Iterable[MemoryRecord] = (),
        recent_writes: Iterable[MemoryWriteEvent] = (),
    ) -> MemoryWriteDecision:
        """Decide whether `content` may reach persistent storage.

        `existing` (e.g. `store.load()`) enables the near-duplicate check;
        `recent_writes` enables the echo check on recent `agent-auto` writes.
        """
        reasons: list[str] = []
        tags_set = {t.strip().lower() for t in tags if t}
        text = (content or "").strip()

        # Frozen source is refused first: PolicyGate never sees memory writes
        # from the knowledge pipeline, so this is the only brake on them.
        if (source or "").strip().lower() in self.frozen_sources:
            return MemoryWriteDecision(
                "reject",
                [
                    (f"auto-memory writes frozen in this context "
                    f"(source='{source}' needs a human checkpoint)")
                ],
            )

        # Hard blocks: never save, regardless of consent.
        if not text:
            return MemoryWriteDecision("reject", ["empty content"])

        if len(text) < MIN_CONTENT_LEN:
            return MemoryWriteDecision("reject", [f"too short (<{MIN_CONTENT_LEN} chars)"])

        if len(text) > MAX_CONTENT_LEN:
            return MemoryWriteDecision("reject", [f"too long (>{MAX_CONTENT_LEN} chars)"])

        # All secret hits are surfaced so the audit trail records every signal.
        is_secret, secret_reasons = contains_secret(text)
        if is_secret:
            return MemoryWriteDecision("reject", secret_reasons)

        has_pii, pii_reasons = contains_pii(text)
        if has_pii and SENSITIVE_DATA_CONSENT_TAG not in tags_set:
            return MemoryWriteDecision(
                "reject",
                [*pii_reasons, f"sensitive data requires explicit '{SENSITIVE_DATA_CONSENT_TAG}' tag"],
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

        if source != "user-explicit" and not (tags_set & CONSENT_TAGS):
            return MemoryWriteDecision(
                "reject",
                [
                    ("no consent signal: source must be 'user-explicit' or "
                    f"tags must include one of {sorted(CONSENT_TAGS)}")
                ],
            )

        owner_normalised = (owner or "").strip().lower() or "self"
        if owner_normalised not in FIRST_PARTY_OWNERS and CROSS_OWNER_CONSENT_TAG not in tags_set:
            return MemoryWriteDecision(
                "reject",
                [
                    (f"owner='{owner}' is third-party and tag "
                    f"'{CROSS_OWNER_CONSENT_TAG}' is missing")
                ],
            )

        # Echo gate: catches the agent re-stating its own recent lessons, which
        # dedup misses because it has no clock and no notion of source.
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

        existing_list = list(existing)
        if existing_list:
            from core.memory_hygiene import DEFAULT_DEDUP_THRESHOLD, find_duplicate

            match = find_duplicate(
                text, existing_list, threshold=DEFAULT_DEDUP_THRESHOLD
            )
            if match is not None:
                rec, score = match
                return MemoryWriteDecision(
                    "reject",
                    [
                        (f"duplicate of {rec.id} "
                        f"(similarity={score:.2f} >= {DEFAULT_DEDUP_THRESHOLD})")
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

#: Общий с `core/topic_tokens.py` словарь, чтобы подсистемы не расходились (MIR-008).
_STOPWORDS: frozenset[str] = _TOPIC_STOPWORDS

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


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if t.lower() not in _STOPWORDS and len(t) > 1}


def _query_tokens(text: str) -> set[str]:
    tokens = _tokens(text)
    if is_broad_project_self_knowledge_question(text):
        tokens |= _BROAD_PROJECT_MEMORY_TOKENS
    # Questions come in Russian while memory is mostly English; without
    # translation word-overlap scoring never matches them.
    tokens |= english_terms_for(tokens)
    return tokens


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


#: BM25 со стандартными параметрами (k1=1.2, b=0.75, IDF в форме Lucene):
#: без поправки на длину длинные записи выигрывали любой вопрос.
_BM25_K1 = 1.2
_BM25_B = 0.75
_PREFIX_LEN = 4


def _term_counts(text: str, tags: Iterable[str]) -> Counter[str]:
    counts = Counter(
        t for t in (w.lower() for w in _TOKEN_RE.findall(text or ""))
        if t not in _STOPWORDS and len(t) > 1
    )
    counts.update(_tag_tokens(tags or ()))
    return counts


def _term_frequency(term: str, counts: Counter[str], prefixes: Counter[str]) -> int:
    """Сколько раз слово вопроса встречается в записи.

    Без точного совпадения засчитывается одно по первым четырём буквам
    (русские окончания: «уроки» и «урок»).
    """
    exact = counts.get(term, 0)
    if exact or len(term) < _PREFIX_LEN:
        return exact
    return 1 if prefixes.get(term[:_PREFIX_LEN]) else 0


def _bm25_scores(q_tokens: set[str], docs: list[Counter[str]]) -> list[float]:
    """BM25 каждой записи против вопроса; IDF считается по этому же хранилищу."""
    n_docs = len(docs)
    if not n_docs or not q_tokens:
        return [0.0] * n_docs
    prefixes = [Counter(t[:_PREFIX_LEN] for t in d if len(t) >= _PREFIX_LEN) for d in docs]
    lengths = [sum(d.values()) for d in docs]
    avgdl = (sum(lengths) / n_docs) or 1.0
    scores = [0.0] * n_docs
    for q in q_tokens:
        row = [_term_frequency(q, d, pre) for d, pre in zip(docs, prefixes, strict=True)]
        df = sum(1 for f in row if f)
        if not df:
            continue
        idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
        for i, f in enumerate(row):
            if f:
                norm = 1.0 - _BM25_B + _BM25_B * lengths[i] / avgdl
                scores[i] += idf * f * (_BM25_K1 + 1.0) / (f + _BM25_K1 * norm)
    return scores


#: Свежесть по Generative Agents (Park et al. 2023): затухание 0.995 за час.
_RECENCY_DECAY_PER_HOUR = 0.995


def _recency(record: MemoryRecord, now: datetime) -> float:
    created_at = record.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    hours = max(0.0, (now - created_at).total_seconds() / 3600.0)
    return _RECENCY_DECAY_PER_HOUR ** hours


def _min_max(values: list[float]) -> list[float]:
    """Нормировка в [0, 1], как у Generative Agents; все равны — все нули."""
    lo, hi = min(values), max(values)
    if hi <= lo:
        return [0.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def _record_prompt_note(record: MemoryRecord) -> str:
    if not _is_readme_record(record):
        return ""
    if _is_live_status_record(record):
        return "[historical README/reference; confirm with recent memory/logs before treating as current] "
    return "[README architecture/reference] "


@dataclass(frozen=True)
class RetrievalSelection:
    """What `select` kept, and rejection counts by reason (not per record; absent reason = zero)."""

    selected: list[MemoryRecord]
    rejected_by: dict[str, int]


#: Прибавка за совпадение рода работы (core/work_kinds.py), соразмерна паре совпавших слов.
_WORK_KIND_BONUS = 2


#: Начало слепка одного обмена («Вопрос: … Вывод: …»), а не переносимого урока:
#: слепки выигрывают по дословным словам вопроса и вытесняют уроки.
_TRANSCRIPT_PREFIXES: tuple[str, ...] = ("вопрос:", "question:")


def _is_transcript(text: str) -> bool:
    """Слепок одного обмена, а не урок; не штрафуется, но квотируется и не идёт в канал рода работы."""
    return (text or "").strip().lower().startswith(_TRANSCRIPT_PREFIXES)


class MemoryRetrievalPolicy:
    """Picks the few persistent records most relevant to the current question."""

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


    #: Места для уроков по роду работы — отдельный канал сверх max_records:
    #: прибавка к баллу тонула в ничьих между десятками записей того же рода.
    lessons_by_kind: int = 2
    #: Предел слепков «Вопрос: … Вывод: …» в основном канале.
    transcripts_in_main: int = 1
    #: Сколько ближайших по смыслу записей проходит без общих слов с вопросом
    #: (только при включённом core/memory_embeddings).
    semantic_candidates: int = 3

    def _take_with_transcript_quota(self, scored: list) -> list[MemoryRecord]:
        """Первые max_records по баллу, но слепков — не больше квоты."""
        out: list[MemoryRecord] = []
        transcripts = 0
        for _score, r in scored:
            if len(out) >= self.max_records:
                break
            if _is_transcript(r.content if isinstance(r.content, str) else str(r.content)):
                if transcripts >= self.transcripts_in_main:
                    continue
                transcripts += 1
            out.append(r)
        return out

    def _add_lessons_by_work_kind(
        self,
        selected: list[MemoryRecord],
        records: list[MemoryRecord],
        q_kinds: frozenset[str],
        relevance: list[float] | None = None,
    ) -> list[MemoryRecord]:
        """Дописывает к отобранному уроки того же рода работы (без слепков обменов).

        Порядок как у Generative Agents: нормированные релевантность + свежесть
        с равными весами; одна свежесть отдавала места случайным свежим записям.
        Без `relevance` — просто самые свежие.
        """
        if not q_kinds or self.lessons_by_kind <= 0:
            return selected
        already = {id(r) for r in selected}
        pool = [
            (i, r) for i, r in enumerate(records)
            if id(r) not in already
            and not _is_transcript(r.content if isinstance(r.content, str) else str(r.content))
            and (q_kinds & work_kinds(r.content if isinstance(r.content, str) else str(r.content)))
        ]
        if not pool:
            return selected
        now = datetime.now(timezone.utc)
        fresh = _min_max([_recency(r, now) for _i, r in pool])
        rel = _min_max([relevance[i] for i, _r in pool]) if relevance else [0.0] * len(pool)
        order = sorted(
            range(len(pool)),
            # Ничья (при двух кандидатах 1+0 против 0+1) решается релевантностью, не датой.
            key=lambda k: (rel[k] + fresh[k], rel[k], pool[k][1].created_at),
            reverse=True,
        )
        return selected + [pool[k][1] for k in order[: self.lessons_by_kind]]

    def select_with_report(
        self,
        records: list[MemoryRecord],
        question: str,
    ) -> RetrievalSelection:
        if not records:
            return RetrievalSelection(selected=[], rejected_by={})
        q_tokens = _query_tokens(question) if question else set()
        if not q_tokens:
            # Not "below_threshold": the question, not the store, had nothing to match.
            return RetrievalSelection(
                selected=[], rejected_by={"no_query_tokens": len(records)}
            )

        below_threshold = 0
        scored: list[tuple[float, MemoryRecord]] = []
        q_kinds = work_kinds(question)
        # Допуск — по числу общих слов (min_score); BM25 решает только порядок.
        broad = is_broad_project_self_knowledge_question(question)
        texts = [r.content if isinstance(r.content, str) else str(r.content) for r in records]
        relevance = _bm25_scores(q_tokens, [
            _term_counts(t, r.tags or []) for t, r in zip(texts, records, strict=True)
        ])
        # Смысл поверх слов; при выключенных эмбеддингах остаётся один BM25.
        from core.memory_embeddings import fused_relevance, top_by_meaning
        relevance, semantic = fused_relevance(question, texts, relevance)
        by_meaning = frozenset() if broad else top_by_meaning(semantic, self.semantic_candidates)
        for i, (r, rel) in enumerate(zip(records, relevance, strict=True)):
            text = r.content if isinstance(r.content, str) else str(r.content)
            r_tokens = _tokens(text)
            score = len(q_tokens & r_tokens)
            tag_tokens = _tag_tokens(r.tags or [])
            score += len(q_tokens & tag_tokens)
            # Prefix match for Russian inflections ("уроки" vs "урок").
            if score == 0:
                all_record_tokens = r_tokens | tag_tokens
                for q in q_tokens:
                    if len(q) >= 4:
                        for rt in all_record_tokens:
                            if len(rt) >= 4 and (rt.startswith(q[:4]) or q.startswith(rt[:4])):
                                score += 1
                                break
            if broad:
                score += _broad_project_score_adjustment(r, score)
            if score >= self.min_score or i in by_meaning:
                scored.append((score if broad else rel, r))
            else:
                below_threshold += 1

        scored.sort(key=lambda pair: (pair[0], pair[1].created_at), reverse=True)
        selected = self._take_with_transcript_quota(scored)
        selected = self._add_lessons_by_work_kind(selected, records, q_kinds, relevance)

        # over_limit is kept apart from below_threshold: raise the cap vs. write better records.
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
                text = cut_keeping_provenance(text, self.per_record_chars)
            tag_str = ",".join(r.tags) if r.tags else "-"
            lines.append(f"- [{r.id} | tags: {tag_str}] {note}{text}")
        return "\n".join(lines)
