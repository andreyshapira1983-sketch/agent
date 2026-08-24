"""Episodic, procedural and consolidation memory for autonomous operation."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from core.completion_marker import sanitize_token
from core.ids import new_id
from core.redaction import redact_dlp_text
from core.state_integrity import (
    append_state_jsonl_unlocked,
    read_state_jsonl_unlocked,
    rewrite_state_jsonl_unlocked,
    state_file_lock,
)
from core.topic_tokens import (
    FLAT,
    STOPWORDS,
    TokenSalience,
    discriminating_tokens,
    topic_tokens,
)

EpisodeOutcome = Literal["success", "partial", "failed"]

# Task completion, kept deliberately apart from `EpisodeOutcome`: `outcome`
# answers "were the claims supported", this answers "was the goal reached".
# A cycle can be impeccably supported and still have answered nothing.
CompletionState = Literal[
    "achieved",
    "partially_achieved",
    "blocked",
    "refused",
    "failed",
    "cancelled",
    "unknown",
]

# What the synthesizer may declare about its own run. `cancelled` and
# `unknown` are absent on purpose: they are facts about the run's termination
# that the loop observes, never something the answer gets to claim.
CompletionDeclaration = Literal[
    "achieved", "partially_achieved", "blocked", "refused", "failed"
]

_COMPLETION_STATES: frozenset[str] = frozenset(CompletionState.__args__)
_COMPLETION_DECLARATIONS: frozenset[str] = frozenset(CompletionDeclaration.__args__)

#: Declarations in which the run states it did NOT deliver the task. These
#: are admissions, not evidence verdicts, so `episode_from_agent_cycle`
#: refuses to bank them as `success` however well the non-delivery was cited.
#: DERIVED from the vocabulary rather than listed, so the set is fail-closed:
#: a declaration added later counts as non-delivery until someone names it a
#: delivery below. `partially_achieved` IS a delivery — of a part.
_DELIVERY_DECLARATIONS: frozenset[str] = frozenset({"achieved", "partially_achieved"})
_NON_DELIVERY_DECLARATIONS: frozenset[str] = _COMPLETION_DECLARATIONS - _DELIVERY_DECLARATIONS
ProcedureStatus = Literal["candidate", "active", "needs_review", "obsolete"]

# A newly distilled procedure is unproven: born `candidate`, kept out of
# ordinary planning retrieval, and promoted to `active` only by a SECOND,
# independent, completed+verified success. Demotion is unchanged, and its
# check comes first so a doubted procedure is never re-labelled candidate.
_PROMOTION_MIN_SUCCESSES = 2

# The real status vocabulary, derived from the type rather than restated:
# operator-facing tallies enumerate THIS, so a hardcoded list goes stale.
PROCEDURE_STATUSES: tuple[str, ...] = ProcedureStatus.__args__


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


# Laplace (add-one) smoothing prior for procedure confidence, so confidence
# reflects how much EVIDENCE backs a workflow and not the raw ratio: without
# it one success reads 1.0. Beta(1,1) puts one success at 0.667 and lets
# confidence climb toward — but never reach — 1.
_CONF_PRIOR_SUCCESS: float = 1.0
_CONF_PRIOR_FAILURE: float = 1.0


def _smoothed_confidence(success_count: int, failure_count: int) -> float:
    """Beta(1,1)-smoothed success probability, rounded to 3 dp."""
    numerator = success_count + _CONF_PRIOR_SUCCESS
    denominator = success_count + failure_count + _CONF_PRIOR_SUCCESS + _CONF_PRIOR_FAILURE
    return round(numerator / denominator, 3)


#: Резка живёт в `core/topic_tokens.py` вместе с весом слова: это одна работа —
#: превратить текст в разрешающий сигнал. Имя оставлено прежним, его зовут из
#: двух десятков мест этого файла.
_tokens = topic_tokens


@dataclass(frozen=True)
class EpisodeRecord:
    """A compact memory of one completed agent cycle or operator task."""

    goal: str
    question: str
    outcome: EpisodeOutcome
    summary: str
    tools_used: tuple[str, ...] = ()
    source_labels: tuple[str, ...] = ()
    verified_chunks: int = 0
    unverified_chunks: int = 0
    # Chunks the verifier could not confirm as faithful support: sub-agent
    # asserted, cited-but-unmatched, receipt-missing, topic-only. They lower
    # quality and block a clean "success" — an answer leaning on unconfirmed
    # support is not a reusable skill.
    weak_chunks: int = 0
    replan_exhausted: bool = False
    # Quality score: verified / (verified + unverified + weak), clamped to [0, 1].
    # None when no evidence chunks were seen at all — the fraction is undefined,
    # NOT perfect. Computed from the chunk counts; not stored in the JSONL.
    answer_quality_score: float | None = None
    #: Про тот ли вопрос ответ. `None` = не измеряли, и это НЕ провал. Зачем:
    #: docs/CODE_NOTES.md, «Cited, scored, admitted — and off topic».
    relevance_score: float | None = None
    tags: tuple[str, ...] = ()
    # Full answer text — stored verbatim for the episodic fast path.
    # Empty string for episodes created before this field was added.
    full_answer: str = ""
    # Which logical task this episode served, and which attempt produced it.
    # `task_id` survives a retry; `run_id` is fresh per attempt. Empty string
    # on legacy records and on runs that carry no task.
    task_id: str = ""
    run_id: str = ""
    # May this episode steer later answers? THREE states, deliberately not a
    # bool: None = legacy row (written before the field), False = quarantined
    # (an explicit decision to withhold), True = eligible. Collapsing None
    # into False would hide the difference. Retrieval admits only True.
    usage_eligible: bool | None = None
    # Procedures that actually influenced THIS run — judged from execution, not
    # from the plan. THREE states: None = legacy row, attribution unknown and
    # nothing may be inferred from it; () = this version ran and is certain no
    # procedure was applied; (ids…) = application observed. MIR-048 debits
    # these ids, so a looser meaning would turn feedback into misattribution.
    used_procedure_ids: tuple[str, ...] | None = None
    # What the synthesizer declared about reaching the goal, verbatim. Stored
    # because it cannot be recovered: the marker is stripped before the answer
    # is verified or shown, so it never reaches `full_answer`. None means no
    # declaration was produced — legacy row, no marker, or no synthesis at all.
    declared_completion: CompletionDeclaration | None = None
    # The verdict, ASSEMBLED AT BANKING and frozen. Stored rather than derived
    # on read because procedural feedback is applied once, under the rule in
    # force at the time; recomputing would silently reclassify episodes whose
    # credit or debit is already spent. None = never classified; readers go
    # through `effective_completion`, which maps None → "unknown".
    completion_state: CompletionState | None = None
    # Defect signals this run raised about ITSELF — the sensors that fired
    # while it worked, recorded because each sensor otherwise logged its
    # verdict and dropped it. THREE states, same convention as the two fields
    # above: None = a row written before this axis existed, nothing may be
    # inferred; () = this version ran and no sensor fired; (names…) = fired.
    #
    # Authority is PER SIGNAL, never blanket. `obligation_silently_missing`
    # IS authoritative: at banking it lowers `achieved` to
    # `partially_achieved` and so withholds procedure credit. Every other
    # member — `reasoning_action_mismatch` included — decides nothing today.
    # Adding a member does NOT grant it power; only naming it in the verdict
    # rule table does, and that is the operator's call.
    defect_signals: tuple[str, ...] | None = None
    # The authoritative fact that displaced this run's own claim, when one did.
    # None = the claim stood (or there was no claim). `declared_completion` is
    # NEVER edited to match: the operator's rule is that the signal wins the
    # operational outcome without erasing the self-assessment, so both survive
    # and the disagreement between them is readable as its own fact.
    completion_override: str | None = None
    id: str = field(default_factory=lambda: new_id("ep"))
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "usage_eligible": self.usage_eligible,
            "used_procedure_ids": (
                None if self.used_procedure_ids is None
                else list(self.used_procedure_ids)
            ),
            "goal": self.goal,
            "question": self.question,
            "outcome": self.outcome,
            "summary": self.summary,
            "full_answer": self.full_answer,
            "tools_used": list(self.tools_used),
            "source_labels": list(self.source_labels),
            "verified_chunks": self.verified_chunks,
            "unverified_chunks": self.unverified_chunks,
            "weak_chunks": self.weak_chunks,
            "replan_exhausted": self.replan_exhausted,
            "tags": list(self.tags),
            "created_at": self.created_at,
            # Omit-when-None rather than an explicit null: an absent key is the
            # honest encoding of "this row predates the axis", and it keeps a
            # legacy row byte-identical to what it was.
            **({} if self.declared_completion is None
               else {"declared_completion": self.declared_completion}),
            **({} if self.completion_state is None
               else {"completion_state": self.completion_state}),
            **({} if self.defect_signals is None
               else {"defect_signals": list(self.defect_signals)}),
            **({} if self.completion_override is None
               else {"completion_override": self.completion_override}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodeRecord:
        return cls(
            id=str(data.get("id") or new_id("ep")),
            task_id=str(data.get("task_id") or ""),
            run_id=str(data.get("run_id") or ""),
            # `.get` WITHOUT a default: an absent key must stay None
            # (legacy_unclassified), never collapse into False (quarantined).
            usage_eligible=(
                None
                if data.get("usage_eligible") is None
                else bool(data["usage_eligible"])
            ),
            used_procedure_ids=(
                None
                if data.get("used_procedure_ids") is None
                else tuple(str(x) for x in data["used_procedure_ids"])
            ),
            # Read back as stored, never re-assembled. A legacy row carries no
            # key and stays None even when it holds an abort tag or
            # `replan_exhausted`: reconstructing a verdict here would hand one
            # to episodes whose feedback was already applied without it.
            # An unrecognised token is refused rather than trusted.
            declared_completion=(
                data.get("declared_completion")
                if data.get("declared_completion") in _COMPLETION_DECLARATIONS
                else None
            ),
            completion_state=(
                data.get("completion_state")
                if data.get("completion_state") in _COMPLETION_STATES
                else None
            ),
            # Absent key stays None (legacy row), an empty list stays () — the
            # difference between "we never looked" and "we looked and saw
            # nothing" is the whole point of recording this.
            defect_signals=(
                None
                if data.get("defect_signals") is None
                else tuple(str(x) for x in data["defect_signals"])
            ),
            completion_override=(
                None
                if data.get("completion_override") is None
                else str(data["completion_override"])
            ),
            goal=str(data.get("goal") or ""),
            question=str(data.get("question") or ""),
            outcome=_episode_outcome(str(data.get("outcome") or "partial")),
            summary=str(data.get("summary") or ""),
            tools_used=tuple(str(x) for x in data.get("tools_used") or ()),
            source_labels=tuple(str(x) for x in data.get("source_labels") or ()),
            verified_chunks=max(0, int(data.get("verified_chunks") or 0)),
            unverified_chunks=max(0, int(data.get("unverified_chunks") or 0)),
            weak_chunks=max(0, int(data.get("weak_chunks") or 0)),
            replan_exhausted=bool(data.get("replan_exhausted", False)),
            answer_quality_score=_compute_quality_score(
                max(0, int(data.get("verified_chunks") or 0)),
                max(0, int(data.get("unverified_chunks") or 0)),
                max(0, int(data.get("weak_chunks") or 0)),
            ),
            tags=tuple(str(x) for x in data.get("tags") or ()),
            full_answer=str(data.get("full_answer") or ""),
            created_at=str(data.get("created_at") or _now_iso()),
        )


@dataclass(frozen=True)
class ProcedureRecord:
    """A reusable workflow distilled from successful episodes."""

    name: str
    workflow_key: str
    trigger_tags: tuple[str, ...]
    steps: tuple[str, ...]
    # One factual line per contributing episode: what was asked, what it
    # worked on, how the claims held. Accumulated rather than overwritten and
    # kept parallel to `source_episode_ids`, because a `workflow_key` pools
    # unrelated runs: a single summary line would describe one situation and
    # silently claim another's successes. Every line comes from what the
    # episode observed — nothing is inferred, and an empty tuple means the
    # run left no honest material.
    lessons: tuple[str, ...] = ()
    source_episode_ids: tuple[str, ...] = ()
    success_count: int = 0
    failure_count: int = 0
    confidence: float = 0.5
    status: ProcedureStatus = "active"
    id: str = field(default_factory=lambda: new_id("proc"))
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "workflow_key": self.workflow_key,
            "trigger_tags": list(self.trigger_tags),
            "steps": list(self.steps),
            "lessons": list(self.lessons),
            "source_episode_ids": list(self.source_episode_ids),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "confidence": self.confidence,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProcedureRecord:
        return cls(
            id=str(data.get("id") or new_id("proc")),
            name=str(data.get("name") or "workflow"),
            workflow_key=str(data.get("workflow_key") or ""),
            # Capped on the way IN as well as on the way out. A record written
            # by an earlier version, or hand-edited, would otherwise keep its
            # unbounded fields — and they are spent on every later run, since
            # the record is injected into planner prompts — until some future
            # credited episode happened to rewrite it.
            trigger_tags=tuple(dict.fromkeys(
                str(x) for x in data.get("trigger_tags") or ()
            ))[:_MAX_TRIGGER_TAGS],
            steps=tuple(str(x) for x in data.get("steps") or ()),
            lessons=_capped_lessons(str(x) for x in data.get("lessons") or ()),
            source_episode_ids=tuple(str(x) for x in data.get("source_episode_ids") or ()),
            success_count=max(0, int(data.get("success_count") or 0)),
            failure_count=max(0, int(data.get("failure_count") or 0)),
            confidence=max(0.0, min(1.0, float(data.get("confidence") or 0.5))),
            status=_procedure_status(str(data.get("status") or "active")),
            created_at=str(data.get("created_at") or _now_iso()),
            updated_at=str(data.get("updated_at") or _now_iso()),
        )

    def with_outcome(self, episode: EpisodeRecord, verdict: str) -> ProcedureRecord:
        """Apply one observation, recomputing every derived value together."""
        success_count = self.success_count + (1 if verdict == "success" else 0)
        failure_count = self.failure_count + (1 if verdict == "failure" else 0)
        confidence = _smoothed_confidence(success_count, failure_count)
        return replace(
            self,
            source_episode_ids=tuple(
                dict.fromkeys([*self.source_episode_ids, episode.id])
            ),
            success_count=success_count,
            failure_count=failure_count,
            confidence=confidence,
            status=_procedure_status_for(
                success_count, confidence, failure_count=failure_count,
            ),
            updated_at=_now_iso(),
        )

    def merged_from_episode(self, episode: EpisodeRecord) -> ProcedureRecord:
        """Fold an episode's PROVENANCE into this procedure without crediting
        it.

        The cap is applied on EVERY return, not only when a lesson is
        appended: an uncredited fold-in must repair an oversized older
        record rather than carry it forward untouched.
        """
        episode_ids = tuple(dict.fromkeys([*self.source_episode_ids, episode.id]))
        lesson = lesson_from_episode(episode)
        lessons = _capped_lessons(self.lessons)
        if lesson and procedure_credit_allowed(episode):
            lessons = _capped_lessons([*self.lessons, lesson])
        return replace(
            self,
            source_episode_ids=episode_ids,
            lessons=lessons,
            updated_at=_now_iso(),
        )

    # Counter-moving fold-in (`with_episode`) no longer exists: counters move
    # only through the causal `apply_episode_feedback` → `with_outcome` path.


@dataclass(frozen=True)
class ConsolidationReport:
    """A small audit record describing how memory evolved."""

    episode_count: int
    procedure_count: int
    linked_episode_ids: tuple[str, ...]
    active_procedure_ids: tuple[str, ...]
    needs_review_procedure_ids: tuple[str, ...]
    obsolete_procedure_ids: tuple[str, ...]
    notes: tuple[str, ...]
    # Added 2026-07-22 with the `candidate` status. Defaulted, so reports
    # written before it round-trip unchanged instead of failing to load.
    candidate_procedure_ids: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: new_id("consol"))
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "episode_count": self.episode_count,
            "procedure_count": self.procedure_count,
            "linked_episode_ids": list(self.linked_episode_ids),
            "active_procedure_ids": list(self.active_procedure_ids),
            "needs_review_procedure_ids": list(self.needs_review_procedure_ids),
            "obsolete_procedure_ids": list(self.obsolete_procedure_ids),
            "candidate_procedure_ids": list(self.candidate_procedure_ids),
            "notes": list(self.notes),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsolidationReport:
        return cls(
            id=str(data.get("id") or new_id("consol")),
            episode_count=max(0, int(data.get("episode_count") or 0)),
            procedure_count=max(0, int(data.get("procedure_count") or 0)),
            linked_episode_ids=tuple(str(x) for x in data.get("linked_episode_ids") or ()),
            active_procedure_ids=tuple(str(x) for x in data.get("active_procedure_ids") or ()),
            needs_review_procedure_ids=tuple(str(x) for x in data.get("needs_review_procedure_ids") or ()),
            obsolete_procedure_ids=tuple(str(x) for x in data.get("obsolete_procedure_ids") or ()),
            candidate_procedure_ids=tuple(str(x) for x in data.get("candidate_procedure_ids") or ()),
            notes=tuple(str(x) for x in data.get("notes") or ()),
            created_at=str(data.get("created_at") or _now_iso()),
        )


@dataclass(frozen=True)
class EpisodeSearchResult:
    """What a search returned, and why the rest of the store did not.

    Same contract as `RetrievalSelection` on the persistent side: reasons are
    reported by the component that decided, counted by reason and never per
    record, and an absent reason means zero.
    """

    episodes: list[EpisodeRecord]
    rejected_by: dict[str, int]


@dataclass(frozen=True)
class ProcedureSearchResult:
    """What a procedure search offered, and why the rest of the store did not.

    Same contract as `EpisodeSearchResult`: reasons counted by reason, never per
    record, absent reason means zero. `excluded_candidate` is the maturity gate
    (a candidate is never offered until a second success promotes it) — reported
    so the candidate-vs-no-match distinction stops being invisible.
    """

    procedures: list[ProcedureRecord]
    rejected_by: dict[str, int]


class EpisodicMemoryStore:
    # Tags that mark episodes as too valuable to evict (e.g. repair lessons).
    PROTECTED_TAGS: frozenset[str] = frozenset({"lesson", "bug-fix", "regression-guard"})

    def __init__(self, path: Path | str, *, max_episodes: int = 200):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_episodes = max_episodes

    def save(self, episode: EpisodeRecord) -> EpisodeRecord:
        """Append one episode, admitting it first. Returns the stored record."""
        admitted = admit_for_storage(episode)
        with state_file_lock(self.path):
            append_state_jsonl_unlocked(self.path, [admitted.to_dict()])
            self._maybe_prune_unlocked()
        return admitted

    def save_once(self, episode: EpisodeRecord) -> bool:
        """Append the episode unless its id is already stored.

        **Bounded idempotency.** The guarantee lasts only while the episode
        is inside the FIFO window (`max_episodes`): once evicted, its id
        becomes writable again. No separate ledger is kept, so this is
        deduplication for a run and its immediate retries — not a permanent
        claim.
        """
        admitted = admit_for_storage(episode)
        with state_file_lock(self.path):
            for row in read_state_jsonl_unlocked(self.path):
                if row.get("id") == episode.id:
                    return False
            append_state_jsonl_unlocked(self.path, [admitted.to_dict()])
            self._maybe_prune_unlocked()
            return True

    def _maybe_prune_unlocked(self) -> int:
        """Evict oldest non-protected episodes when over *max_episodes*.

        Protected episodes (carrying any tag in PROTECTED_TAGS) are never
        evicted.  Returns the number of records removed.
        """
        if self.max_episodes <= 0:
            return 0
        rows = read_state_jsonl_unlocked(self.path)
        if len(rows) <= self.max_episodes:
            return 0
        episodes: list[EpisodeRecord] = []
        for row in rows:
            try:
                episodes.append(EpisodeRecord.from_dict(row))
            except (TypeError, ValueError):
                continue
        protected = [e for e in episodes if self.PROTECTED_TAGS & set(e.tags)]
        evictable = [e for e in episodes if not (self.PROTECTED_TAGS & set(e.tags))]
        to_remove = len(episodes) - self.max_episodes
        if to_remove <= 0:
            return 0
        # Sort evictable by age ascending so oldest are removed first.
        evictable.sort(key=lambda e: e.created_at)
        kept = evictable[to_remove:] + protected
        kept.sort(key=lambda e: e.created_at)
        rewrite_state_jsonl_unlocked(self.path, [e.to_dict() for e in kept])
        return to_remove

    def load(self) -> list[EpisodeRecord]:
        with state_file_lock(self.path):
            rows = read_state_jsonl_unlocked(self.path)
        out: list[EpisodeRecord] = []
        for row in rows:
            try:
                out.append(EpisodeRecord.from_dict(row))
            except (TypeError, ValueError):
                continue
        return out

    def count(self) -> int:
        return len(self.load())

    def search(self, query: str, *, limit: int = 3) -> list[EpisodeRecord]:
        """The matching episodes. Delegates so there is one decision, not two."""
        return self.search_with_report(query, limit=limit).episodes

    def search_with_report(self, query: str, *, limit: int = 3) -> EpisodeSearchResult:
        """`search`, plus why the rest of the store did not come back.

        A caller that only sees the survivors cannot report on the drops that
        happened in here — which is how `episodes_selected=0, rejected_by={}`
        stayed reachable on a store of 200 episodes.
        """
        episodes = self.load()
        q_tokens = _tokens(query)
        if not q_tokens:
            return EpisodeSearchResult(
                episodes=[],
                rejected_by={"no_query_tokens": len(episodes)} if episodes else {},
            )
        scored: list[tuple[int, EpisodeRecord]] = []
        no_overlap = 0
        q_content = q_tokens - STOPWORDS
        for ep in episodes:
            haystack = " ".join([ep.goal, ep.question, ep.summary, " ".join(ep.tags)])
            hay_tokens = _tokens(haystack)
            score = len(q_tokens & hay_tokens)
            # MIR-105/024/008: a match made of function words is not a match.
            # Scoring still counts every shared token (order unchanged); only
            # ELIGIBILITY requires at least one discriminating word — measured
            # 2026-08-22, filler alone retrieved 17 of 34 procedures, and a
            # launch and a deletion scored as one question.
            if score and (q_content & hay_tokens):
                # Boost protected episodes (lessons, bug-fixes) so they surface
                # above ordinary episodes when there is any token overlap.
                if self.PROTECTED_TAGS & set(ep.tags):
                    score += 50
                scored.append((score, ep))
            else:
                no_overlap += 1
        scored.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        selected = [ep for _score, ep in scored[:limit]]
        rejected_by = {
            k: v for k, v in (
                ("no_overlap", no_overlap),
                ("over_limit", len(scored) - len(selected)),
            ) if v > 0
        }
        return EpisodeSearchResult(episodes=selected, rejected_by=rejected_by)

    def search_by_tags(
        self, tags: Iterable[str], *, limit: int = 5
    ) -> list[EpisodeRecord]:
        """Return episodes that carry ALL of the given tags, newest first."""
        required = frozenset(tags)
        if not required:
            return []
        matches = [
            ep for ep in self.load() if required <= set(ep.tags)
        ]
        matches.sort(key=lambda e: e.created_at, reverse=True)
        return matches[:limit]

    def find_most_similar(
        self, query: str, *, threshold: float = 0.35
    ) -> tuple[EpisodeRecord | None, float]:
        """Return the episode whose *question* has the highest Jaccard
        similarity to *query* and the similarity score. Returns ``(None,
        0.0)`` when no episode reaches *threshold*.

        Only the stored ``question`` field is compared (not
        goal/summary/tags), so the signal is «did the user ask THIS before?»
        and not «is this topic familiar?». A candidate with a low
        ``answer_quality_score`` gets the threshold lowered by 0.10.
        """
        # MIR-024: the frame is not the question. Similarity is computed over
        # DISCRIMINATING words only — «Я хочу ЗАПУСТИТЬ АГЕНТА, что мне
        # сделать?» and «Я хочу УДАЛИТЬ ВСЕ ЛОГИ, что мне сделать?» scored
        # 0.400 (the threshold) on the shared frame alone, so a launch was
        # annotated to the planner as a repeat of a deletion. A question made
        # entirely of filler has nothing to compare and matches nothing.
        q_tokens = discriminating_tokens(query)
        if not q_tokens:
            return None, 0.0
        best_ep: EpisodeRecord | None = None
        best_score: float = 0.0
        for ep in self.load():
            ep_tokens = discriminating_tokens(ep.question)
            if not ep_tokens:
                continue
            union = q_tokens | ep_tokens
            if not union:
                continue
            score = len(q_tokens & ep_tokens) / len(union)
            if score > best_score:
                best_score = score
                best_ep = ep
        if best_ep is None:
            return None, 0.0
        # Lower the threshold when the best candidate was a low-quality — or
        # unmeasured — answer. A re-ask hint is an offer to go deeper, not a
        # verdict, and an answer with no evidence is a plausible reason
        # someone is asking again.
        effective_threshold = threshold
        if (
            best_ep.answer_quality_score is None
            or best_ep.answer_quality_score < 0.5
        ):
            effective_threshold = max(0.20, threshold - 0.10)
        if best_score >= effective_threshold:
            return best_ep, best_score
        return None, 0.0

    def prune_stale(
        self,
        *,
        max_age_days: int = 30,
        min_quality: float = 0.4,
        staleness_threshold: float = 1.5,
        dry_run: bool = False,
    ) -> list[str]:
        """Remove old, low-quality, non-protected episodes.

        Wraps :func:`core.episodic_hygiene.prune_stale_episodes`.
        Returns the IDs of evicted episodes.
        """
        # Local import: keeps episodic_hygiene out of the tight import
        # graph of smart_memory.
        from core.episodic_hygiene import prune_stale_episodes

        return prune_stale_episodes(
            self,
            max_age_days=max_age_days,
            min_quality=min_quality,
            staleness_threshold=staleness_threshold,
            dry_run=dry_run,
        )


class ProceduralMemoryStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[ProcedureRecord]:
        with state_file_lock(self.path):
            rows = read_state_jsonl_unlocked(self.path)
        out: list[ProcedureRecord] = []
        for row in rows:
            try:
                out.append(ProcedureRecord.from_dict(row))
            except (TypeError, ValueError):
                continue
        return out

    def rewrite(self, procedures: list[ProcedureRecord]) -> None:
        with state_file_lock(self.path):
            rewrite_state_jsonl_unlocked(self.path, [p.to_dict() for p in procedures])

    def count(self) -> int:
        return len(self.load())

    def apply_episode_feedback(
        self, episode: EpisodeRecord, *, allow_credit: bool = True
    ) -> dict:
        """Feed one run's outcome back to the procedures it actually used.

        `allow_credit=False` withholds the POSITIVE direction only, for a
        cycle whose verifier threw: `verified=0, unverified=0` falls through
        the outcome derivation to `success`, so an unmeasured run would
        otherwise credit a procedure exactly like a verified one. The
        negative direction is untouched — a debit comes from a structural
        failure the verifier had no part in, and suppressing it would let a
        crash shield a procedure that genuinely failed. The suppression is
        reported rather than made to look like an absent verdict.
        """
        used = episode.used_procedure_ids
        verdict = feedback_for_episode(episode)
        credit_suppressed = bool(verdict == "success" and not allow_credit)
        if credit_suppressed:
            verdict = "none"
        report = {
            "applied": 0, "already_applied": 0, "orphaned": 0, "skipped": 0,
            "verdict": verdict,
            "credit_suppressed": credit_suppressed,
            "attribution": "unknown" if used is None else "recorded",
        }
        if not used:
            return report
        if report["verdict"] == "none":
            report["skipped"] = len(dict.fromkeys(used))
            return report

        with state_file_lock(self.path):
            rows = read_state_jsonl_unlocked(self.path)
            procs = []
            for row in rows:
                try:
                    procs.append(ProcedureRecord.from_dict(row))
                except (TypeError, ValueError):
                    continue
            by_id = {p.id: p for p in procs}
            changed = False
            for pid in dict.fromkeys(used):        # collapse duplicates first
                target = by_id.get(pid)
                if target is None:
                    report["orphaned"] += 1
                    continue
                if episode.id in target.source_episode_ids:
                    report["already_applied"] += 1
                    continue
                by_id[pid] = target.with_outcome(episode, report["verdict"])
                report["applied"] += 1
                changed = True
            if changed:
                rewrite_state_jsonl_unlocked(
                    self.path, [by_id.get(p.id, p).to_dict() for p in procs]
                )
        return report

    def recompute_legacy_confidence(
        self, *, dry_run: bool = True, limit: int | None = None
    ) -> dict:
        """Restore `confidence == _smoothed_confidence(success, failure)`
        (MIR-051).

        Touches **only** derived values: `confidence`, and `status` with it,
        since status is exactly `confidence >= 0.6`. Leaving status stale
        would just trade one broken invariant for another.
        """
        with state_file_lock(self.path):
            rows = read_state_jsonl_unlocked(self.path)

        def _dist(values: list) -> dict:
            out: dict = {}
            for v in values:
                key = str(round(float(v), 3)) if isinstance(v, (int, float)) else "invalid"
                out[key] = out.get(key, 0) + 1
            return out

        report = {
            "scanned": len(rows),
            "eligible": 0,
            "corrected": 0,
            "already_consistent": 0,
            "invalid": 0,
            "skipped": 0,
            "before_distribution": _dist([r.get("confidence") for r in rows]),
            "after_distribution": {},
            "dry_run": bool(dry_run),
            "limit_reached": False,
        }

        changed = False
        for row in rows:
            success = row.get("success_count")
            failure = row.get("failure_count")
            if (
                not isinstance(success, int) or isinstance(success, bool)
                or not isinstance(failure, int) or isinstance(failure, bool)
                or success < 0 or failure < 0
            ):
                # Counters that cannot be trusted are reported, never guessed:
                # a repaired confidence over invented history is not a repair.
                report["invalid"] += 1
                continue

            expected = _smoothed_confidence(success, failure)
            stored = row.get("confidence")
            if isinstance(stored, (int, float)) and round(float(stored), 3) == expected:
                report["already_consistent"] += 1
                continue

            report["eligible"] += 1
            if limit is not None and report["corrected"] >= limit:
                # Bounded and resumable: the remainder is picked up next run.
                report["limit_reached"] = True
                report["skipped"] += 1
                continue

            report["corrected"] += 1
            if not dry_run:
                row["confidence"] = expected
                # H-44: у поля была ТРЕТЬЯ власть — эта строка знала только
                # `active`/`needs_review` и `candidate` не производила вовсе,
                # поэтому проход по новорождённым переименовал бы их все.
                row["status"] = _procedure_status_for(
                    int(row.get("success_count") or 0),
                    expected,
                    failure_count=int(row.get("failure_count") or 0),
                )
                changed = True

        report["after_distribution"] = _dist(
            [
                _smoothed_confidence(r["success_count"], r["failure_count"])
                if isinstance(r.get("success_count"), int)
                and isinstance(r.get("failure_count"), int)
                and r["success_count"] >= 0 and r["failure_count"] >= 0
                else r.get("confidence")
                for r in rows
            ]
        )

        if changed and not dry_run:
            with state_file_lock(self.path):
                rewrite_state_jsonl_unlocked(self.path, rows)
        return report

    def upsert_from_episode(self, episode: EpisodeRecord) -> tuple[ProcedureRecord | None, bool]:
        candidate = procedure_from_episode(episode)
        if candidate is None:
            return None, False
        procedures = self.load()
        out: list[ProcedureRecord] = []
        updated: ProcedureRecord | None = None
        created = True
        for proc in procedures:
            if proc.workflow_key == candidate.workflow_key:
                # Merge provenance only — NO credit for a tool-set match.
                # Promotion happens solely via `used_procedure_ids`.
                updated = proc.merged_from_episode(episode)
                out.append(updated)
                created = False
            else:
                out.append(proc)
        if updated is None:
            # A brand-new candidate records its creating episode's PROVENANCE
            # but earns no credit: it was not USED by the run that produced it
            # (it did not exist yet), so its counters stay at birth (zero).
            updated = candidate.merged_from_episode(episode)
            out.append(updated)
        self.rewrite(out)
        return updated, created

    def search(self, query: str, *, limit: int = 3) -> list[ProcedureRecord]:
        """Procedures to offer the planner. Thin wrapper over the reporting form
        so the one production caller can log *why* the rest were dropped."""
        return self.search_with_report(query, limit=limit).procedures

    def search_with_report(
        self, query: str, *, limit: int = 3, salience: TokenSalience = FLAT
    ) -> ProcedureSearchResult:
        """Same as `search`, but it also reports why the store's other
        procedures did not surface — symmetric with
        `EpisodicMemoryStore.search_with_report`.
        """
        procedures = self.load()
        q_tokens = _tokens(query)
        q_content = q_tokens - STOPWORDS
        if not q_tokens:
            return ProcedureSearchResult(
                procedures=[],
                rejected_by={"no_query_tokens": len(procedures)} if procedures else {},
            )
        scored: list[tuple[float, int, ProcedureRecord]] = []
        excluded_retired = 0
        no_overlap = 0
        for proc in procedures:
            # Кандидат ПОКАЗЫВАЕТСЯ: затвор зрелости смешивал видимость с
            # кредитом и запирал круг. Почему и чем это мерялось:
            # docs/CODE_NOTES.md, «The method that could not survive the turn».
            if proc.status in {"obsolete", "needs_review"}:
                excluded_retired += 1
                continue
            haystack = " ".join([proc.name, " ".join(proc.trigger_tags), " ".join(proc.steps)])
            # Взвешенно, а не штуками: три служебных слова не должны обходить
            # одно имя сигнала. Без корпуса вес плоский и счёт прежний.
            hay_tokens = _tokens(haystack)
            score = salience.overlap(q_tokens, hay_tokens)
            if score and (q_content & hay_tokens):
                scored.append((score, 0 if proc.status == "candidate" else 1, proc))
            else:
                no_overlap += 1
        # Уместность первой, зрелость — при РАВНОЙ уместности. Правило
        # «неподтверждённое не вытесняет подтверждённое» сохранено там, где
        # вопрос зрелости и живёт; поверх темы оно стоило точности.
        scored.sort(key=lambda item: (item[0], item[1], item[2].confidence,
                                      item[2].updated_at), reverse=True)
        selected = [proc for _score, _proven, proc in scored[:limit]]
        rejected_by = {
            k: v for k, v in (
                ("excluded_retired", excluded_retired),
                ("no_overlap", no_overlap),
                ("over_limit", len(scored) - len(selected)),
            ) if v > 0
        }
        return ProcedureSearchResult(procedures=selected, rejected_by=rejected_by)


class MemoryConsolidationStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, report: ConsolidationReport) -> None:
        with state_file_lock(self.path):
            append_state_jsonl_unlocked(self.path, [report.to_dict()])

    def load(self) -> list[ConsolidationReport]:
        with state_file_lock(self.path):
            rows = read_state_jsonl_unlocked(self.path)
        out: list[ConsolidationReport] = []
        for row in rows:
            try:
                out.append(ConsolidationReport.from_dict(row))
            except (TypeError, ValueError):
                continue
        return out

    def count(self) -> int:
        return len(self.load())


def procedure_credit_allowed(episode: EpisodeRecord) -> bool:
    """May this run raise a procedure's standing?

    `completion_state` is read FROZEN, through the shared accessor. It is
    never recomputed and never taken from `declared_completion`.
    """
    # Третья ось, общая с допуском эпизода в использование: предикат один на
    # оба рубежа, чтобы они не разошлись.
    if _answer_disqualified(episode):
        return False
    return bool(
        effective_completion(episode) == "achieved"
        and episode.outcome == "success"
        and episode.tools_used
    )


def procedure_debit_allowed(episode: EpisodeRecord) -> bool:
    """May this run lower a procedure's standing?

    Deliberately narrow: only a failure the RUN ITSELF demonstrates. An
    exhausted replan is the loop giving up after trying — a fact about
    execution that the procedure took part in.

    Everything else is neutral: a DECLARED `failed` (the answer's own claim
    about itself), `cancelled` (a control signal), `blocked` / `refused` /
    `partially_achieved` (non-completions the procedure may have executed
    perfectly through), and `unknown` / legacy `None`. An ABORTED run is
    neutral structurally — it carries no `used_procedure_ids`, so there is
    no causal link to debit through.

    The frozen state is authoritative and comes first: `replan_exhausted`
    NARROWS an existing `failed`, it can never create one. A record claiming
    `achieved` while carrying an exhausted replan is not debited.

    Derived `aborted:*` tags are never consulted. A value computed from
    another value must not be what authorises a debit.
    """
    return bool(
        effective_completion(episode) == "failed"
        and episode.replan_exhausted is True
    )


def feedback_for_episode(episode: EpisodeRecord) -> str:
    """What this run's outcome says about a procedure it used: "success",
    "failure" or "none". An evidence-`partial` is NOT a failure — debits
    come only from `procedure_debit_allowed`.
    """
    if procedure_credit_allowed(episode):
        return "success"
    if procedure_debit_allowed(episode):
        return "failure"
    return "none"


def resolve_used_procedures(
    *, selected: list[ProcedureRecord], executed_tools: list[str]
) -> tuple[str, ...]:
    """Which of the SELECTED procedures this run actually applied.

    Two gates, both required. A procedure must have been selected into the
    run (retrieval alone is not use), and every tool in its workflow must
    have actually executed — the plan is not evidence, so a run cancelled
    before reaching a procedure's steps does not debit it.
    """
    ran: list[str] = []
    seen: set[str] = set()
    executed = list(executed_tools)
    for proc in selected:
        if proc.id in seen:
            continue
        workflow = [t for t in proc.workflow_key.removeprefix("tools:").split("->") if t]
        if not workflow:
            continue
        if all(tool in executed for tool in workflow):
            ran.append(proc.id)
            seen.add(proc.id)
    # Order by the point each workflow completed, so the record reflects the
    # sequence of actual use rather than the order retrieval happened to return.
    def _completion_index(pid: str) -> int:
        proc = next(p for p in selected if p.id == pid)
        wf = [t for t in proc.workflow_key.removeprefix("tools:").split("->") if t]
        return max(len(executed) - 1 - executed[::-1].index(t) for t in wf)

    return tuple(sorted(ran, key=_completion_index))


def _checked_declaration(
    raw: str | None,
    on_audit: Callable[[str, dict[str, Any]], None] | None = None,
) -> CompletionDeclaration | None:
    """Last line of defence for a token that bypassed the marker parser."""
    if raw is None or raw in _COMPLETION_DECLARATIONS:
        return raw  # type: ignore[return-value]
    if on_audit is not None:
        try:
            on_audit(
                "completion_declaration_coerced",
                {"token": sanitize_token(raw), "coerced_to": None},
            )
        except Exception:  # noqa: BLE001, S110 — an audit channel must not break banking
            pass
    return None


def assemble_completion_state(
    *,
    aborted_reason: str,
    replan_exhausted: bool,
    declared: str | None,
    obligation_unmet: bool = False,
) -> CompletionState:
    """Decide whether the goal was reached. Called ONCE, at banking.

    A closed table; the ORDER is the contract, first match wins:

    1. aborted_reason == "cancelled"            -> cancelled
    2. aborted_reason (anything else)           -> failed
    3. replan_exhausted                         -> failed
    4. obligation_unmet and declared==achieved  -> partially_achieved
    5. declared is a known token                -> that token
    6. otherwise                                -> unknown

    Row 4 only ever LOWERS a claim of `achieved`; an honest `blocked` or
    `failed` is left where the run put it.
    """
    return assemble_completion_verdict(
        aborted_reason=aborted_reason,
        replan_exhausted=replan_exhausted,
        declared=declared,
        obligation_unmet=obligation_unmet,
    ).state


@dataclass(frozen=True)
class CompletionVerdict:
    """The operational verdict plus the divergence that produced it.

    Operator's rule: an authoritative signal must outrank the run's own
    self-assessment **in the operational outcome**, and must not destroy that
    self-assessment — the disagreement is itself a diagnostic fact and is kept.

    So three things are stored, never two: what the answer claimed
    (`EpisodeRecord.declared_completion`, untouched), what actually holds
    (`state`), and — only when they diverge — which fact displaced the claim
    (`overridden_by`). A reader can always reconstruct both sides and the reason.
    """

    state: CompletionState
    overridden_by: str | None = None

    @property
    def diverged(self) -> bool:
        return self.overridden_by is not None


def assemble_completion_verdict(
    *,
    aborted_reason: str,
    replan_exhausted: bool,
    declared: str | None,
    obligation_unmet: bool = False,
    enforcement_failed: bool = False,
    user_contract_partial: bool = False,
) -> CompletionVerdict:
    """The single rule table. :func:`assemble_completion_state` delegates here.

    `obligation_unmet` is authoritative in one direction only: it can lower a
    claim of `achieved` to `partially_achieved`, never raise anything. A run
    that left a duty silently unmet did not finish the job, whatever it said —
    but a run that already reported `blocked` or `failed` is not made worse by
    it, because the honest report was never the problem.
    """
    if aborted_reason == "cancelled":
        return _displaced("cancelled", declared, "cancelled")
    if aborted_reason:
        return _displaced("failed", declared, "aborted")
    if replan_exhausted:
        return _displaced("failed", declared, "replan_exhausted")
    if obligation_unmet and declared == "achieved":
        return _displaced(
            "partially_achieved", declared, "obligation_silently_missing"
        )
    # Та же односторонняя власть, другое основание: `obligation_unmet` — долг
    # остался невыполненным; здесь — часть контракта оператора модуль вообще
    # не сумел представить. Отказ УДОСТОВЕРЯТЬ не равен утверждению провала:
    # `partially_achieved` и означает «сделано не всё, что просили».
    if user_contract_partial and declared == "achieved":
        return _displaced(
            "partially_achieved", declared, "user_contract_unrepresented"
        )
    # Census A2: the answer-safety check raised, so the run delivered a safe
    # refusal instead of the draft it had written — work happened, the honest
    # outcome reached the user, and the process was defective.
    # `partially_achieved` also withholds procedure credit and eligibility.
    if enforcement_failed and declared == "achieved":
        return _displaced(
            "partially_achieved", declared, "answer_enforcement_failed"
        )
    if declared in _COMPLETION_DECLARATIONS:
        return CompletionVerdict(declared)  # type: ignore[arg-type]
    return CompletionVerdict("unknown")


def _displaced(
    state: CompletionState, declared: str | None, reason: str
) -> CompletionVerdict:
    """Name the displacing fact only when there was a claim to displace.

    A run that declared nothing, or that declared exactly what holds, has no
    divergence to record — writing a reason there would invent a disagreement.
    """
    if declared in _COMPLETION_DECLARATIONS and declared != state:
        return CompletionVerdict(state, reason)
    return CompletionVerdict(state)


def effective_completion(episode: EpisodeRecord) -> CompletionState:
    """The completion verdict a reader should act on.

    `None` on the record means the episode was never classified — it was
    written before the axis existed. Readers get `unknown` for it, which is
    fail-closed everywhere the gates land: an unclassified episode steers
    nothing, credits nothing and is not replayed. The stored `None` is kept
    distinct from a stored `"unknown"` so a legacy row remains recognisable as
    legacy rather than as a run we examined and could not classify.
    """
    state = episode.completion_state
    return state if state in _COMPLETION_STATES else "unknown"  # type: ignore[return-value]


#: Сигналы дефекта, при которых ответ НЕЛЬЗЯ обращать в опыт — ни как эпизод,
#: пригодный к использованию, ни как заслугу процедуры. Список ОДИН на оба
#: рубежа: порознь эти предикаты уже расходились. Сюда попадает только то,
#: что говорит о ЛОЖНОСТИ ответа, и растёт он по доказанному вреду, не по
#: подозрению.
DISQUALIFYING_DEFECT_SIGNALS: frozenset[str] = frozenset(
    {"self_contradiction", "content_refuted", "citation_fabricated"}
)


def _answer_disqualified(episode: EpisodeRecord) -> bool:
    """Опровергал ли этот ответ сам себя — единый вопрос для обоих рубежей."""
    return bool(
        DISQUALIFYING_DEFECT_SIGNALS & set(episode.defect_signals or ())
    )


def _lesson_provenance_disqualified(episode: EpisodeRecord) -> bool:
    """The two axes a `lesson` may NOT waive: its sources and its subject.

    Kept beside `_answer_disqualified` because both answer the same shape of
    question — is this record admissible at all — as opposed to the outcome
    axes, which ask how the run went.
    """
    if any(str(label).startswith("memory:") for label in episode.source_labels):
        return True
    from core.verification_summary import _LOW_RELEVANCE

    return (
        episode.relevance_score is not None
        and episode.relevance_score < _LOW_RELEVANCE
    )


def decide_usage_eligibility(episode: EpisodeRecord) -> bool:
    """Decide whether a freshly banked episode may steer later answers.

    Admitted only when ALL hold: `completion_state == "achieved"` (read
    frozen, never re-derived, never from the declaration); `outcome ==
    "success"`; `verified_chunks > 0` (something was independently
    confirmed); and not a replay. One exception: a curated `lesson` is
    admitted whatever its outcome.

    Always returns a bool — `None` means "never classified" and belongs to
    rows written before the field existed. No threshold constant appears
    here on purpose: every rule reads a fact the verifier measured.
    """
    # ПЕРЕД всеми остальными осями, включая исключение для урока: ответ,
    # который сам себя опроверг, не становится опытом ни на каком основании.
    # Такой ответ проходит КАЖДУЮ проверку ниже, потому что верификация меряет
    # разрешимость ссылки, а не истинность референта (MIR-060).
    if _answer_disqualified(episode):
        return False
    if "lesson" in episode.tags:
        # The exemption now waives what it always PROMISED to waive and no
        # more. Failing legitimately costs an episode its outcome, its
        # completion and its verified chunks — a run that failed confirmed
        # nothing — and remembering that failure as a warning is the whole
        # point of the tag (101 of 127 lessons in the live store are failures).
        # It does NOT excuse two axes that have nothing to do with failing:
        #   * a `memory:` source is memory citing itself — an echo is not a
        #     second witness, and it is the amplification step a memory
        #     poisoning attack needs (MIR-115 measured the bypass, MIR-121
        #     the threat class);
        #   * an off-topic record is off-topic whether or not it failed.
        # Measured before the change: zero live lessons are affected, so this
        # is prophylaxis on the attack path rather than repair of live damage.
        return not _lesson_provenance_disqualified(episode)
    if episode.outcome != "success":
        return False
    # The second axis: `outcome` reports that the claims held up, which a
    # blocked non-answer can satisfy perfectly. Both must agree — neither
    # substitutes for the other — and this one only ever subtracts permission.
    # Read from the FROZEN state through the shared accessor, never from
    # `declared_completion`.
    if effective_completion(episode) != "achieved":
        return False
    if any(str(label).startswith("memory:") for label in episode.source_labels):
        return False
    # Третья ось: первые две спрашивают, устояли ли утверждения, эта — про тот
    # ли они вопрос. Порог общий с предупреждением оператору, из замера.
    from core.verification_summary import _LOW_RELEVANCE

    if episode.relevance_score is not None and episode.relevance_score < _LOW_RELEVANCE:
        return False
    return episode.verified_chunks > 0


def is_usage_eligible(episode: EpisodeRecord) -> bool:
    """May this episode steer a later answer?

    Only an EXPLICIT `True` admits it. Both `None` (legacy_unclassified) and
    `False` (quarantined) are refused — fail-closed, because an episode whose
    provenance was never established is not evidence that it is trustworthy.
    """
    return episode.usage_eligible is True


def admit_for_storage(episode: EpisodeRecord) -> EpisodeRecord:
    """Resolve the admission verdict for an episode about to be written.

    ``None`` means "no one has decided yet" and is the only state this
    resolves. An explicit ``True``/``False`` from the caller is a decision
    already taken and is passed through untouched — that is how an aborted
    run stays quarantined even though the policy is willing to look at it.

    Idempotent, so calling it at the write site *and* inside the store is a
    no-op the second time. Applied on **write only**: a ``None`` read back
    off disk is a row that predates the field, and re-deciding it now would
    rewrite history with today's rule.
    """
    if episode.usage_eligible is None:
        episode = replace(
            episode, usage_eligible=decide_usage_eligibility(episode)
        )
    # The completion axis, same boundary: every NEW record carries an explicit
    # verdict. A writer that knows its outcome writes it; one that cannot
    # classify writes the explicit ``"unknown"``. Absence of the field is
    # legal only when READING rows that predate the axis. Write-only, like the
    # eligibility rule above: a `None` read back off disk stays `None`.
    if episode.completion_state is None:
        episode = replace(episode, completion_state="unknown")
    return episode


def episode_id_for_run(run_id: str) -> str:
    """Deterministic episode id for one attempt.

    Derived rather than random so `EpisodicMemoryStore.save_once` can detect a
    duplicate of the same run without a side ledger.
    """
    return f"ep-run-{run_id}"


def _derive_episode_outcome(
    *,
    aborted_reason: str,
    replan_exhausted: bool,
    declared_completion: str | None,
    verified: int,
    unverified: int,
    weak: int,
) -> EpisodeOutcome:
    """The evidence axis, decided in one place. The ORDER is the contract:

    1. the run did not finish — no chunk count can express that;
    2. replanning was exhausted — same;
    3. the run SAID it did not deliver — an admission, not a verdict;
    4. otherwise the counters judge the support the answer had.
    """
    if aborted_reason:
        # The run did not finish. Decided before the counters and never
        # overridden into a success by them.
        return "failed"
    if replan_exhausted:
        return "failed"
    if declared_completion in _NON_DELIVERY_DECLARATIONS:
        # A well-cited non-delivery used to count as a well-cited success:
        # outcome came from chunk counts alone, so the request asked for work,
        # the answer said it was not done, and memory recorded a success.
        # `usage_eligible=False` does not repair that — the row still READS
        # as a success.
        return "failed" if declared_completion == "failed" else "partial"
    if unverified > verified:
        # A relative-majority test (mirrors the `weak >= verified` guard
        # below), not a magic threshold: a single lucky verified chunk must
        # not immunise an answer with many more unverified ones.
        # verified=0/unverified=0 is untouched — general knowledge stays
        # `success`.
        return "partial"
    if weak > 0 and weak >= verified:
        # The answer leans at least as much on support the verifier could not
        # confirm (sub-agent claims, unmatched citations, missing receipts) as
        # on verified evidence. Not a clean success — do NOT let it graduate to
        # a reusable procedure or bank a 1.0 quality score.
        return "partial"
    return "success"


def episode_from_agent_cycle(  # noqa: PLR0913 — flat: depth 1, all 1 returns are guard clauses
    *,
    goal: str,
    question: str,
    answer: str,
    tools_used: Iterable[str],
    source_labels: Iterable[str],
    verified_chunks: int = 0,
    unverified_chunks: int = 0,
    weak_chunks: int = 0,
    replan_exhausted: bool = False,
    run_id: str = "",
    task_id: str = "",
    usage_eligible: bool | None = None,
    aborted_reason: str = "",
    used_procedure_ids: tuple[str, ...] | None = None,
    declared_completion: str | None = None,
    defect_signals: Iterable[str] | None = None,
    relevance_score: float | None = None,
    on_audit: Callable[[str, dict[str, Any]], None] | None = None,
) -> EpisodeRecord:
    """Build an episode from one finished cycle.

    `aborted_reason` marks a run that never completed (an exception, a
    cancellation, a timeout). Outcome is otherwise derived from chunk counts
    alone, which cannot see any of those — so without this an interrupted run
    would bank as `success`.
    """
    verified = max(0, int(verified_chunks))
    unverified = max(0, int(unverified_chunks))
    weak = max(0, int(weak_chunks))
    outcome = _derive_episode_outcome(
        aborted_reason=aborted_reason,
        replan_exhausted=replan_exhausted,
        declared_completion=declared_completion,
        verified=verified,
        unverified=unverified,
        weak=weak,
    )
    tools = tuple(str(t) for t in tools_used if str(t).strip())
    labels = tuple(str(label) for label in source_labels if str(label).strip())
    tags = _episode_tags(tools=tools, outcome=outcome, labels=labels)
    if aborted_reason:
        tags = tags + ("aborted", f"aborted:{aborted_reason}")
    # A run-derived id is what makes duplicate detection possible at all: the
    # store can recognise "this attempt was already banked" without keeping a
    # ledger. Runs without an id keep the random default.
    episode_id = episode_id_for_run(run_id) if run_id else new_id("ep")
    # Normalised once, here, because two things read it: the stored column and
    # the verdict below — the fact that decides is the very fact recorded, not
    # a parallel recomputation. Stripped BEFORE the blank filter, not after:
    # otherwise a whitespace-only entry becomes an unnameable member of a list
    # whose membership decides a verdict.
    signals = (
        None if defect_signals is None
        else tuple(dict.fromkeys(
            cleaned for s in defect_signals if (cleaned := str(s).strip())
        ))
    )
    _verdict = assemble_completion_verdict(
        aborted_reason=str(aborted_reason or ""),
        replan_exhausted=bool(replan_exhausted),
        declared=declared_completion,
        obligation_unmet="obligation_silently_missing" in (signals or ()),
        user_contract_partial=bool(
            {"user_contract_unrepresented", "named_units_unaddressed"}
            & set(signals or ())
        ),
        enforcement_failed="answer_enforcement_failed" in (signals or ()),
    )
    return EpisodeRecord(
        goal=_clean_text(goal, max_chars=300),
        question=_clean_text(question, max_chars=400),
        outcome=outcome,
        summary=_clean_text(answer, max_chars=900),
        full_answer=answer,
        tools_used=tools,
        source_labels=labels,
        verified_chunks=verified,
        unverified_chunks=unverified,
        weak_chunks=weak,
        replan_exhausted=bool(replan_exhausted),
        answer_quality_score=_compute_quality_score(verified, unverified, weak),
        relevance_score=relevance_score,
        tags=tags,
        task_id=str(task_id or ""),
        run_id=str(run_id or ""),
        # Defaults to None (legacy_unclassified): banking an episode is not by
        # itself a verdict that it may steer later answers. The caller decides.
        usage_eligible=usage_eligible,
        used_procedure_ids=used_procedure_ids,
        # Both completion fields are settled here, at the moment of banking,
        # and never recomputed afterwards (MIR-057). An unrecognised token is
        # dropped rather than stored, so the declaration column can only ever
        # hold something the parser is allowed to produce.
        declared_completion=_checked_declaration(declared_completion, on_audit),
        completion_state=_verdict.state,
        completion_override=_verdict.overridden_by,
        # Order preserved, duplicates dropped: a sensor that fires on three
        # attempts is one fault, and a stable order keeps two runs with the same
        # faults byte-comparable. `None` passes through untouched so a caller
        # that cannot collect signals stays distinguishable from one that
        # collected none.
        defect_signals=signals,
        id=episode_id,
    )


# Bounds on the two fields that accumulate across episodes. A common
# `workflow_key` pools every credited run into one record, so both fields
# would otherwise grow without limit — and the growth is paid again on every
# later run, since the record is rewritten to JSONL and injected into planner
# prompts. Unbounded tags also destroy retrieval: a record carrying every
# token matches every query. The most RECENT lessons are kept.
_MAX_LESSONS = 12
_MAX_TRIGGER_TAGS = 40
#: Evidence labels shown in one line, before a "+N more" summary. Shared by the
#: lesson and the step so the two cannot drift apart.
_MAX_SHOWN_LABELS = 3


def _capped_lessons(lessons: Iterable[str]) -> tuple[str, ...]:
    deduped = tuple(dict.fromkeys(x for x in lessons if x))
    return deduped[-_MAX_LESSONS:]


def _summarise_labels(labels: tuple[str, ...]) -> str:
    """`a, b, c, +N more` — bounded, and identical wherever labels are shown."""
    if not labels:
        return ""
    shown = ", ".join(labels[:_MAX_SHOWN_LABELS])
    hidden = len(labels) - _MAX_SHOWN_LABELS
    return f"{shown}, +{hidden} more" if hidden > 0 else shown


def lesson_from_episode(episode: EpisodeRecord) -> str:
    """One factual line about what this run met and how it went. Never
    inferred.
    """
    asked = _clean_text(episode.question, max_chars=160)
    if not asked:
        return ""
    parts = [f'asked: "{asked}"']
    if episode.tools_used:
        parts.append("via " + "->".join(episode.tools_used))
    if episode.source_labels:
        parts.append(f"over {_summarise_labels(episode.source_labels)}")
    claims = episode.verified_chunks + episode.unverified_chunks
    if claims:
        parts.append(f"{episode.verified_chunks}/{claims} claims verified")
    # Faults are part of the causal record: a workflow that worked *despite* an
    # observed fault is a different thing from one that ran clean, and hiding
    # the difference is how a flawed method gets reused as a good one.
    if episode.defect_signals:
        parts.append("observed: " + ", ".join(episode.defect_signals))
    return "; ".join(parts)


def procedure_from_episode(episode: EpisodeRecord) -> ProcedureRecord | None:
    # Same predicate as the counter and the verdict: a workflow is only worth
    # minting from a run that finished the job. Widening admission would bank
    # better-written lessons behind the same false successes.
    if not procedure_credit_allowed(episode):
        return None
    # Left as the tool sequence on purpose. `resolve_used_procedures` parses it
    # back into that sequence to decide which procedures actually ran, so the
    # key is load-bearing for attribution. Its known pooling defect (MIR-050)
    # is why `lessons` accumulates instead of holding one summary line.
    workflow_key = "tools:" + "->".join(episode.tools_used)
    lesson = lesson_from_episode(episode)
    subject = _clean_text(episode.question, max_chars=60)
    readable_tools = ", ".join(episode.tools_used)
    steps = (
        # The situation first: a step list that starts with the tools answers
        # "how" without ever saying "for what".
        f"Situation: {subject}" if subject else "Situation: (not recorded)",
        *(f"Run tool: {tool}" for tool in episode.tools_used),
        # Bounded by the same helper as the lesson: this string is stored
        # verbatim and scored during retrieval, so an unbounded sweep over many
        # files would otherwise put an arbitrarily long line into both.
        (
            "Evidence gathered: " + _summarise_labels(episode.source_labels)
            if episode.source_labels
            else "Evidence gathered: (none recorded)"
        ),
        "Verify every claim against that evidence before answering.",
    )
    return ProcedureRecord(
        name=f"{subject} via {readable_tools}" if subject else f"Workflow using {readable_tools}",
        workflow_key=workflow_key,
        # Question tokens join the tags so retrieval can match on what the
        # workflow was FOR: scoring reads name/tags/steps, and tool names
        # alone score zero against a query about the subject. Tokenised from
        # the FULL question, never the 60-char display subject.
        trigger_tags=tuple(dict.fromkeys([
            *episode.tools_used, *episode.tags, *sorted(_tokens(episode.question)),
        ]))[:_MAX_TRIGGER_TAGS],
        steps=tuple(steps),
        lessons=(lesson,) if lesson else (),
        source_episode_ids=(),
        success_count=0,
        failure_count=0,
        confidence=0.5,
        # Born unproven. `upsert_from_episode` folds in the first episode via
        # `merged_from_episode` — provenance and lesson only, no counter and
        # no status change. Promotion comes solely from the causal feedback
        # path, so a fresh procedure stays `candidate`, never `active`.
        status="candidate",
    )


def consolidate_memory(
    *,
    episodes: list[EpisodeRecord],
    procedures: list[ProcedureRecord],
) -> ConsolidationReport:
    active = tuple(proc.id for proc in procedures if proc.status == "active")
    needs_review = tuple(proc.id for proc in procedures if proc.status == "needs_review")
    obsolete = tuple(proc.id for proc in procedures if proc.status == "obsolete")
    candidates = tuple(proc.id for proc in procedures if proc.status == "candidate")
    linked_episode_ids = tuple(
        dict.fromkeys(ep_id for proc in procedures for ep_id in proc.source_episode_ids)
    )
    notes: list[str] = []
    if not episodes:
        notes.append("no episodes recorded yet")
    if not procedures:
        notes.append("no successful tool workflows have become procedures yet")
    if needs_review:
        notes.append("some procedures need review because success confidence is low")
    if len(linked_episode_ids) < len(episodes):
        notes.append("some episodes are observations only and are not procedural skills")
    if not notes:
        notes.append("episodic and procedural memory are linked")
    return ConsolidationReport(
        episode_count=len(episodes),
        procedure_count=len(procedures),
        linked_episode_ids=linked_episode_ids,
        active_procedure_ids=active,
        needs_review_procedure_ids=needs_review,
        obsolete_procedure_ids=obsolete,
        candidate_procedure_ids=candidates,
        notes=tuple(notes),
    )


def format_experience_context(
    *,
    episodes: list[EpisodeRecord],
    procedures: list[ProcedureRecord],
    max_chars: int = 2_400,
) -> str:
    if not episodes and not procedures:
        return ""
    lines = ["<agent_experience_memory>"]
    if procedures:
        lines.append("procedures:")
        for proc in procedures:
            lines.append(
                f"- [{proc.id}] {proc.name}; confidence={proc.confidence}; "
                f"status={proc.status}; steps={' | '.join(proc.steps[:5])}"
            )
    if episodes:
        lines.append("episodes:")
        for ep in episodes:
            lines.append(
                f"- [{ep.id}] outcome={ep.outcome}; tools={','.join(ep.tools_used) or 'none'}; "
                f"summary={ep.summary}"
            )
    lines.append("</agent_experience_memory>")
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "..."
    return text


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
