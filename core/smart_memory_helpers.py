"""Helpers extracted verbatim from ``core/smart_memory.py`` by the incremental
splitter. The original module re-exports every name below, so all
existing import paths keep working."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Literal

from core.redaction import redact_dlp_text
from core.topic_tokens import topic_tokens

EpisodeOutcome = Literal["success", "partial", "failed"]

CompletionState = Literal[
    "achieved",
    "partially_achieved",
    "blocked",
    "refused",
    "failed",
    "cancelled",
    "unknown",
]

CompletionDeclaration = Literal[
    "achieved", "partially_achieved", "blocked", "refused", "failed"
]

ProcedureStatus = Literal["candidate", "active", "needs_review", "obsolete"]

_PROMOTION_MIN_SUCCESSES = 2

def _procedure_status_for(
    success_count: int, confidence: float, *, failure_count: int = 0,
) -> ProcedureStatus:
    """Статус процедуры по её опыту. Незнание — не приговор.

    H-44 в docs/audit/HISTORICAL_FAILURE_LEDGER.md. Прежняя редакция смотрела
    только на уверенность и потому сваливала «ещё не проверено» и «проверено и
    не годится» в один исход. У ни разу не запускавшейся процедуры уверенность
    — это ПРИОР (0.5), а не результат, и `needs_review` для неё означал бы
    приговор без суда.

    Цена измерена, а не предположена: `needs_review` исключается из выдачи, а
    `candidate` используется. В живой памяти 24 процедуры из 34 — ровно
    новорождённые, и проход-починка по прежнему правилу отключил бы 71 %
    процедурной памяти, при том что ни один факт о них не изменился.

    `failure_count` с умолчанием: старые вызывающие продолжают работать, а
    послабление действует только там, где ОБА счётчика нулевые.
    """
    if success_count == 0 and failure_count == 0:
        return "candidate"
    if confidence < 0.6:
        return "needs_review"
    if success_count < _PROMOTION_MIN_SUCCESSES:
        return "candidate"
    return "active"

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _clean_text(text: str, *, max_chars: int = 800) -> str:
    redacted, _, _ = redact_dlp_text(str(text or "").strip())
    redacted = " ".join(redacted.split())
    if len(redacted) > max_chars:
        return redacted[: max_chars - 1].rstrip() + "..."
    return redacted

def _compute_quality_score(
    verified: int, unverified: int, weak: int = 0
) -> float | None:
    """Fraction of evidence chunks that stood up as verified support.

    ``weak`` counts claims the verifier could NOT confirm as faithful
    support — sub-agent-asserted, cited-but-unmatched, receipt-missing and
    topic-only chunks. They belong in the denominator, never in the
    numerator: an answer resting on unconfirmed support must not score as if
    it were verified.
    """
    total = verified + unverified + weak
    if total == 0:
        return None
    return round(verified / total, 3)

_tokens = topic_tokens

_ADMISSIBLE_COMPLETIONS = frozenset({"achieved", "partially_achieved"})

def episode_id_for_run(run_id: str) -> str:
    """Deterministic episode id for one attempt.

    Derived rather than random so `EpisodicMemoryStore.save_once` can detect a
    duplicate of the same run without a side ledger.
    """
    return f"ep-run-{run_id}"

_MAX_LESSONS = 12

_MAX_TRIGGER_TAGS = 40

_MAX_SOURCE_QUESTIONS = 5

_MAX_SHOWN_LABELS = 3

def _capped_lessons(lessons: Iterable[str]) -> tuple[str, ...]:
    deduped = tuple(dict.fromkeys(x for x in lessons if x))
    return deduped[-_MAX_LESSONS:]

def _capped_source_questions(questions: Iterable[str]) -> tuple[str, ...]:
    """ПЕРВЫЕ пять, а не последние: вытесняем поздние приросты, не происхождение.

    Обратное правило `_capped_lessons` (последние N) здесь было бы порчей: урок
    тем ценнее, чем свежее, а вопрос-происхождение — тем, что он первый.
    """
    deduped = tuple(dict.fromkeys(_clean_text(x, max_chars=200) for x in questions if x))
    return tuple(x for x in deduped if x)[:_MAX_SOURCE_QUESTIONS]

def _summarise_labels(labels: tuple[str, ...]) -> str:
    """`a, b, c, +N more` — bounded, and identical wherever labels are shown."""
    if not labels:
        return ""
    shown = ", ".join(labels[:_MAX_SHOWN_LABELS])
    hidden = len(labels) - _MAX_SHOWN_LABELS
    return f"{shown}, +{hidden} more" if hidden > 0 else shown

def _episode_outcome(value: str) -> EpisodeOutcome:
    return value if value in {"success", "partial", "failed"} else "partial"  # type: ignore[return-value]

def _procedure_status(value: str) -> ProcedureStatus:
    return value if value in {"candidate", "active", "needs_review", "obsolete"} else "needs_review"  # type: ignore[return-value]

def _episode_tags(
    *,
    tools: tuple[str, ...],
    outcome: EpisodeOutcome,
    labels: tuple[str, ...],
) -> tuple[str, ...]:
    tags: list[str] = ["episode", outcome]
    tags.extend(tools)
    if any(label.startswith("web") for label in labels):
        tags.append("web")
    if any(label.startswith("file") for label in labels):
        tags.append("file")
    return tuple(dict.fromkeys(tags))


def episode_tools(planned_sources: Iterable[dict[str, Any]], executed: Iterable[str]) -> list[str]:
    """Инструменты хода для эпизода: план последнего круга плюс исполненное раньше.

    С кругом наблюдения (`core/observation_round.py`) последний план часто пуст —
    «всё сделано», — и эпизод записывал пустой список за ход, который читал и
    писал (замер 2026-09-19). Без кругов всё исполненное входит в последний план,
    и список остаётся прежним.
    """
    planned = [s["tool"] for s in planned_sources]
    return planned + [t for t in dict.fromkeys(executed) if t not in planned]
