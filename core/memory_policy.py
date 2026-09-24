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
    the current cycle. BM25 ranking over a keyword-overlap floor, recency
    tiebreaker, capped count + capped per-record length.

Both policies are pure functions over MemoryRecords + question text and are
fully testable on their own.
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

# `core.hygiene` is imported lazily inside `decide` to avoid an import cycle
# when `core/hygiene.py` later wants to reach into models / policies.

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


#: Строка родословной записи: «Источник: URL (прочитан …)» / «Источники: file:…».
_PROVENANCE_LINE = re.compile(r"^(?:Источник|Источники):[^\n]*$", re.MULTILINE)


def cut_keeping_provenance(text: str, limit: int) -> str:
    """Укоротить запись до `limit`, сохранив её последнюю строку родословной.

    Цепочка веб-знания, 2026-09-19: строка «Источник: …» стоит в конце записи,
    и срез по 400 символам отрезал её у КАЖДОЙ записи вывода — модели велено
    «подтверди у источника», а источника она не видела. Укорачивается середина.
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
        """`frozen_sources` names write sources that are blocked in this
        context (run-scoped). Used by the operator brake to freeze agent-
        initiated ("agent-auto") memory writes so the agent cannot silently
        grow its own persistent memory without a human in the loop. Empty by
        default — existing behaviour is unchanged. User writes
        (source='user-explicit') are never frozen unless explicitly listed.
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

        `existing` lets the policy refuse near-duplicates of records already
        on disk. Pass `store.load()` from the caller — the policy never
        reads the store itself, keeping it a pure function over inputs.

        `recent_writes` is the time-windowed rolling log of recent `agent-
        auto` writes (from `core.memory_echo_antibody`). When supplied, the
        Memory Echo Antibody (A1) refuses an `agent-auto` record that merely
        re-states something the agent already wrote in the last window — the
        "echo chamber" failure mode. `user-explicit` writes are never
        affected.
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
                    (f"auto-memory writes frozen in this context "
                    f"(source='{source}' needs a human checkpoint)")
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

        # --- consent gate (must be user-sourced OR remember-worthy tag) --

        if source != "user-explicit" and not (tags_set & CONSENT_TAGS):
            return MemoryWriteDecision(
                "reject",
                [
                    ("no consent signal: source must be 'user-explicit' or "
                    f"tags must include one of {sorted(CONSENT_TAGS)}")
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
                    (f"owner='{owner}' is third-party and tag "
                    f"'{CROSS_OWNER_CONSENT_TAG}' is missing")
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

# Words that add no signal to keyword overlap scoring.
#: Один словарь на обе подсистемы: перенесён в `core/topic_tokens.py`
#: 2026-08-22 (MIR-008) — расхождение было именно здесь.
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
    # The operator asks in Russian, memory is written in English, and scoring is
    # word overlap — so the two never met. Measured on the live store:
    # "кто владеет архитектурой?" 0 records, "who owns the architecture?" 3.
    # The bilingual set above only fired for broad self-knowledge questions.
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


#: BM25 — стандарт лексического поиска (Robertson & Zaragoza, «The Probabilistic
#: Relevance Framework: BM25 and Beyond»). Параметры — умолчания из той же
#: литературы: k1 в [1.2, 2.0], b = 0.75; IDF в форме Lucene, всегда > 0.
#:
#: Зачем, замерено 2026-09-23: балл был ЧИСЛОМ ОБЩИХ СЛОВ, без поправки на
#: длину записи и на редкость слова. Длинная запись с большим словарём
#: выигрывала любой вопрос: нужный урок всплывал 5 раз из 9 на памяти до
#: 14:30 и 0 из 9 к вечеру, когда в хранилище легли разборы по 2400 знаков
#: против обычных 770. BM25 делит частоту слова на длину записи относительно
#: средней (b) и взвешивает слово его редкостью в хранилище (IDF).
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

    Точное совпадение — как раньше. Если точного нет, засчитывается ОДНО
    совпадение по первым четырём буквам (русские окончания: «уроки» и «урок»)
    — то же правило, что было, только теперь по каждому слову, а не на всю
    запись разом.
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


#: Свежесть по Generative Agents (Park et al. 2023, arXiv 2304.03442):
#: экспоненциальное затухание 0.995 за час.
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
    """What `select` kept, and why each of the rest did not make it.

    Counts are aggregated **by reason, never per record** — a per-record
    trace would grow with the store and cost more than the retrieval it
    observes. A reason that did not fire is absent, not zero.
    """

    selected: list[MemoryRecord]
    rejected_by: dict[str, int]


#: РОД РАБОТЫ — чем человек сейчас занят, а не какими словами он это назвал.
#:
#: Замер 2026-09-23 на 186 записях: нужный урок всплывал 2 раза из 9 (22%).
#: Отбор шёл по пересечению СЛОВ, и разные слова об одном деле не встречались:
#: «поставь библиотеку» не доставало урок «ставя себе пакет, проверь окружение»
#: (библиотека против пакета), «посчитай, сколько стоит заказ» не доставало
#: урок про написание денежных чисел (заказ против валюты). Урок, который не
#: всплывает в тот момент, когда он нужен, работой не является — ровно та
#: беда, за которую оператор ловил агента: «записал промах вечером, повторил
#: наутро».
#:
#: Словарь НАРОЧНО мал и закрыт. Он не заменяет совпадение слов, а добавляется
#: к нему: род работы — подсказка, а не приговор, и запись без рода теряет
#: ровно ничего.
_WORK_KINDS: dict[str, tuple[str, ...]] = {
    # правка кода и починка дефектов
    "patch": (
        "правк", "почин", "чини", "исправ", "дефект", "поломк", "ошибк",
        "баг", "коммит", "тест", "регресс", "сломал", "упал",
    ),
    # замер, сравнение, «стало ли лучше»
    "measure": (
        "замер", "измер", "мера", "мерит", "сравн", "проб", "лучше", "хуже",
        "процент", "среднее", "распредел", "статист", "выборк",
    ),
    # деньги, заказы, расход
    "money": (
        "доллар", "валют", "сумм", "цена", "стоит", "стоим", "заказ",
        "ставк", "расход", "бюджет", "оплат", "счёт", "тариф", "окупа",
    ),
    # установка инструментов себе
    "install": (
        "пакет", "библиотек", "постав", "установ", "окружен", "зависимост",
        "интерпрет", "версия", "ввоз", "импорт",
    ),
    # разговор с человеком
    "speak": (
        "оператор", "человек", "сказать", "спросить", "застря", "упёр",
        "обращ", "внимани", "голос", "сообщ", "написать ему",
    ),
    # улики, источники, проверка фактов
    "source": (
        "источник", "улик", "ссылк", "цитат", "подтвержд", "провер",
        "факт", "утвержд", "сверк",
    ),
}

#: Прибавка за совпадение рода. Соразмерна паре совпавших слов: подсказка
#: поднимает нужную запись над случайной, но не вытесняет запись, которая
#: прямо отвечает на вопрос.
_WORK_KIND_BONUS = 2



#: Начало записи, которая является СЛЕПКОМ ОДНОГО ОБМЕНА, а не переносимым
#: знанием: «Вопрос: … Вывод: …». Замер 2026-09-23: таких 98 из 186, то есть
#: больше половины хранилища. Они выигрывают отбор по построению — содержат
#: дословные слова вопроса, — и вытесняют обобщённые уроки. Замерено в
#: исследовании обучения агентов: обобщённые уроки дают +6.5%, сырые записи
#: попыток МИНУС 9.5%, то есть вредят.
_TRANSCRIPT_PREFIXES: tuple[str, ...] = ("вопрос:", "question:")


def _is_transcript(text: str) -> bool:
    """Слепок одного обмена, а не урок.

    Такие записи НЕ выбрасываются и не штрафуются: когда спрашивают именно о
    том обмене, слепок — верный ответ, и совпадение слов его поднимет. Он
    только не получает подсказку по РОДУ РАБОТЫ: род работы отвечает на
    вопрос «как это делается», а слепок отвечает «что однажды спросили».
    """
    return (text or "").strip().lower().startswith(_TRANSCRIPT_PREFIXES)


#: Признак должен НАЧИНАТЬ слово, а не прятаться внутри чужого.
#:
#: Замер 2026-09-23, первая редакция этого словаря: искали подстроку, и род
#: «деньги» получили почти все записи — потому что «цена» лежит внутри
#: «оценка», а «счёт» внутри «счётчик». Подсказка, которая срабатывает на всё,
#: не отличает ничего: нужный урок остался на том же месте, что и до неё.
_WORK_KIND_RE: dict[str, re.Pattern[str]] = {
    kind: re.compile(
        r"(?<![0-9а-яёa-z])(?:" + "|".join(markers) + r")",
        re.IGNORECASE,
    )
    for kind, markers in _WORK_KINDS.items()
}


def work_kinds(text: str) -> frozenset[str]:
    """Роды работы, на которые похож этот текст."""
    lowered = (text or "").lower()
    if not lowered:
        return frozenset()
    return frozenset(
        kind for kind, pattern in _WORK_KIND_RE.items()
        if pattern.search(lowered)
    )


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


    #: Сколько мест отдано УРОКАМ ПО РОДУ РАБОТЫ — отдельно от мест,
    #: которые занимают записи, отвечающие на сам вопрос.
    #:
    #: Замер 2026-09-23 показал, почему нужен отдельный канал, а не прибавка
    #: к баллу. Прибавка давала +2 КАЖДОЙ записи того же рода, а их в
    #: хранилище десятки: ранжирование начинали решать ничьи, и нужный урок
    #: оставался там же, где был. Отбор по вопросу и напоминание по роду
    #: работы — две РАЗНЫЕ задачи, и делить между ними три места значит не
    #: решать ни одной.
    #:
    #: Цена: две записи по 400 знаков к подсказке. Против неё — замеренная
    #: беда «записал промах вечером, повторил наутро»: урок, который не
    #: всплывает в момент работы, работой не является.
    lessons_by_kind: int = 2
    #: Сколько записей, ближайших по СМЫСЛУ, допускается в отбор без общих
    #: слов с вопросом (гибридный поиск: кандидаты — объединение выдач обоих
    #: поисков). Иначе перефразировка без единого общего слова («поставь
    #: библиотеку» и «ставя себе пакет») не доходит даже до ранжирования.
    #: Работает, только когда включён поиск по смыслу (core/memory_embeddings).
    semantic_candidates: int = 3

    def _add_lessons_by_work_kind(
        self,
        selected: list[MemoryRecord],
        records: list[MemoryRecord],
        q_kinds: frozenset[str],
        relevance: list[float] | None = None,
    ) -> list[MemoryRecord]:
        """Дописывает к отобранному уроки того же рода работы.

        Порядок — как у Generative Agents (Park et al. 2023): релевантность
        вопросу плюс свежесть, обе нормированы в [0, 1], веса равны. Свежесть
        держится потому, что урок тем вернее описывает нынешний код, чем позже
        он записан, а устаревший урок бьёт по трудным задачам сильнее, чем
        помогает (замерено: минус 26%). Но ОДНА свежесть — не отбор: 2026-09-23
        канал отдавал оба места последним записанным разборам, какими бы ни
        был вопрос, и нужный урок не всплывал ни разу из девяти. Важность в
        сумму не входит: поле importance у нас не измеряется. Без релевантности
        (`relevance is None`) остаётся прежний порядок — самые свежие.

        Слепки обменов («Вопрос: … Вывод: …») сюда не попадают: род работы
        отвечает на «как это делается», а слепок — на «что однажды спросили».
        Их в хранилище больше половины (98 из 186 на 2026-09-23), и без этого
        отсечения канал наполнился бы ими.
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
            # Ничья решается релевантностью: при двух кандидатах нормировка
            # даёт одному 1+0, другому 0+1, и дата отдала бы место свежему,
            # но не относящемуся к делу — ровно измеренный сбой.
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
            # Nothing was judged about the records at all. Calling this
            # "below threshold" points the reader at the store when the
            # question is what produced no searchable tokens.
            return RetrievalSelection(
                selected=[], rejected_by={"no_query_tokens": len(records)}
            )

        below_threshold = 0
        scored: list[tuple[float, MemoryRecord]] = []
        # РОД РАБОТЫ вопроса — считается один раз, не на каждую запись.
        q_kinds = work_kinds(question)
        # Порог допуска остался прежним (число общих слов >= min_score):
        # кто проходил, тот проходит. BM25 решает только ПОРЯДОК. Ветка
        # «широкий вопрос о проекте» сохраняет свою настроенную прибавку.
        broad = is_broad_project_self_knowledge_question(question)
        texts = [r.content if isinstance(r.content, str) else str(r.content) for r in records]
        relevance = _bm25_scores(q_tokens, [
            _term_counts(t, r.tags or []) for t, r in zip(texts, records, strict=True)
        ])
        # Смысл поверх слов (core/memory_embeddings.py): выпуклая сумма
        # нормированных баллов; выключен — остаётся один BM25.
        from core.memory_embeddings import fused_relevance, top_by_meaning
        relevance, semantic = fused_relevance(question, texts, relevance)
        by_meaning = frozenset() if broad else top_by_meaning(semantic, self.semantic_candidates)
        for i, (r, rel) in enumerate(zip(records, relevance, strict=True)):
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
            if broad:
                score += _broad_project_score_adjustment(r, score)
            if score >= self.min_score or i in by_meaning:
                scored.append((score if broad else rel, r))
            else:
                below_threshold += 1

        # Higher score first, then newer first.
        scored.sort(key=lambda pair: (pair[0], pair[1].created_at), reverse=True)
        selected = [r for _score, r in scored[: self.max_records]]
        selected = self._add_lessons_by_work_kind(selected, records, q_kinds, relevance)

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
                text = cut_keeping_provenance(text, self.per_record_chars)
            tag_str = ",".join(r.tags) if r.tags else "-"
            lines.append(f"- [{r.id} | tags: {tag_str}] {note}{text}")
        return "\n".join(lines)
