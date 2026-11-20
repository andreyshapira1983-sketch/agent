"""Episodic, procedural and consolidation memory for autonomous operation."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from core.completion_marker import sanitize_token
from core.ids import new_id
from core.smart_memory_helpers import (  # noqa: F401 -- re-exported
    _ADMISSIBLE_COMPLETIONS,
    _MAX_LESSONS,
    _MAX_SHOWN_LABELS,
    _MAX_SOURCE_QUESTIONS,
    _MAX_TRIGGER_TAGS,
    _PROMOTION_MIN_SUCCESSES,
    CompletionDeclaration,
    CompletionState,
    EpisodeOutcome,
    ProcedureStatus,
    _capped_lessons,
    _capped_source_questions,
    _clean_text,
    _compute_quality_score,
    _episode_outcome,
    _episode_tags,
    _now_iso,
    _procedure_status,
    _procedure_status_for,
    _summarise_labels,
    _tokens,
    episode_id_for_run,
    episode_tools,
)
from core.state_integrity import (
    append_state_jsonl_unlocked,
    read_state_jsonl_unlocked,
    rewrite_state_jsonl_unlocked,
    state_file_lock,
)
from core.topic_tokens import FLAT, STOPWORDS, TokenSalience, discriminating_tokens

# Task completion, kept apart from `EpisodeOutcome`: `outcome` says whether the claims were supported, this says whether the goal was reached.

# What the synthesizer may declare about its own run; `cancelled` and `unknown` are absent on purpose (facts about termination that the loop observes, not for the answer to claim).

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

# A newly distilled procedure is born `candidate`, kept out of ordinary planning retrieval, and promoted to `active` only by a SECOND independent completed+verified success.
# Demotion's check comes first so a doubted procedure is never re-labelled candidate.

# The real status vocabulary, derived from the type: operator-facing tallies enumerate THIS so a hardcoded list cannot go stale.
PROCEDURE_STATUSES: tuple[str, ...] = ProcedureStatus.__args__


# Laplace (add-one) smoothing prior: confidence reflects how much EVIDENCE backs a workflow, not the raw ratio
# (one success reads 0.667, not 1.0, and climbs toward but never reaches 1).
_CONF_PRIOR_SUCCESS: float = 1.0
_CONF_PRIOR_FAILURE: float = 1.0


def _smoothed_confidence(success_count: int, failure_count: int) -> float:
    """Beta(1,1)-smoothed success probability, rounded to 3 dp."""
    numerator = success_count + _CONF_PRIOR_SUCCESS
    denominator = success_count + failure_count + _CONF_PRIOR_SUCCESS + _CONF_PRIOR_FAILURE
    return round(numerator / denominator, 3)


_STANDING_RANK = {"needs_review": 0, "candidate": 1, "active": 2}


def _repaired_status(stored: Any, success: int, failure: int, confidence: float) -> str:
    """Status a counter repair may write: lower or keep, never raise.

        `obsolete` is untouched; `needs_review` (maybe a deliberate demotion) is freed only for the exact 1:1 tie the old rule retired;
        an unknown status is capped at `candidate`.
    """
    derived = _procedure_status_for(success, confidence, failure_count=failure)
    if stored == "obsolete":
        return stored
    if stored == "needs_review":
        return derived if (success, failure) == (1, 1) and derived == "candidate" else stored
    cap = _STANDING_RANK.get(stored, _STANDING_RANK["candidate"])
    return derived if _STANDING_RANK[derived] <= cap else (
        stored if stored in _STANDING_RANK else "candidate"
    )


#: Резка живёт в `core/topic_tokens.py` вместе с весом слова: это одна работа —
#: превратить текст в разрешающий сигнал. Имя оставлено прежним, его зовут из
#: двух десятков мест этого файла.


#: Date given to a stored row that carries none: "unknown, so old". Reading it
#: as "now" made every such row look fresh at each read (проверка 16.11, п. 9).
_UNKNOWN_TIME = "1970-01-01T00:00:00+00:00"

#: How many contributing episode ids one procedure keeps (the newest). The
#: episodic store holds 200 by default, so older ids point at nothing anyway
#: (проверка 16.11, п. 10).
_MAX_SOURCE_EPISODE_IDS = 200


def _legacy_id(prefix: str, data: dict[str, Any]) -> str:
    """Stable id for a stored row that has none: the same row reads the same id every time, so idempotency checks recognise it."""
    import hashlib
    import json

    digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:32]
    return f"{prefix}-legacy-{digest}"


def _capped_episode_ids(ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ids))[-_MAX_SOURCE_EPISODE_IDS:]


def _str_items(raw: Any) -> tuple[str, ...]:
    """A stored list field as strings. A bare string is ONE item: iterating it split a signal name into letters and the disqualifying signal was never seen."""
    if raw is None or raw == "":
        return ()
    if isinstance(raw, (str, bytes)):
        return (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw,)
    if isinstance(raw, dict):
        return tuple(str(k) for k in raw)
    # A number where a list belongs is a broken row, not one item: raising keeps the row unreadable,
    # so rewrites preserve it instead of pruning it.
    return tuple(str(x) for x in raw)


def _stored_flag(raw: Any) -> bool | None:
    """A flag read from disk by its meaning: `bool("false")` is True, which turned the text "false" into permission. Unknown -> None."""
    if raw is None or isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no", ""}:
        return False
    return None


def _stored_confidence(raw: Any, success: int, failure: int) -> float:
    """Confidence read from disk: a real number in [0, 1] is kept as is (0.0 included); anything else (missing, NaN, text) is recomputed from the counters."""
    import math

    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return _smoothed_confidence(success, failure)
    if not math.isfinite(value) or raw is None:
        return _smoothed_confidence(success, failure)
    return max(0.0, min(1.0, value))


def _stored_relevance(raw: Any) -> float | None:
    """Relevance read from disk: a number in [0, 1] or "not measured".

        A non-number or an out-of-range number is "not measured", not zero: zero is a verdict and must not be invented for a corrupted row.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if 0.0 <= value <= 1.0 else None


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
    # Chunks the verifier could not confirm as faithful support (sub-agent asserted, cited-but-unmatched, receipt-missing, topic-only).
    # They lower quality and block a clean "success".
    weak_chunks: int = 0
    replan_exhausted: bool = False
    # Quality score: verified / (verified + unverified + weak), clamped to [0, 1]. None when no evidence chunks were seen
    # (undefined, NOT perfect) or when the counts describe a rejected draft. Computed, not stored in the JSONL.
    answer_quality_score: float | None = None
    #: Про тот ли вопрос ответ. `None` = не измеряли, и это НЕ провал. Зачем:
    #: docs/CODE_NOTES.md, «Cited, scored, admitted — and off topic».
    relevance_score: float | None = None
    tags: tuple[str, ...] = ()
    # Full answer text, stored verbatim for the episodic fast path; empty for episodes created before this field.
    full_answer: str = ""
    # Which logical task this episode served and which attempt produced it: `task_id` survives a retry, `run_id` is fresh per attempt.
    # Empty on legacy records and on runs without a task.
    task_id: str = ""
    run_id: str = ""
    # Family link: every episode born inside one tick carries that tick's trace id, so a success episode and the declined
    # product of the SAME tick can be joined at read time. Empty on legacy rows.
    trace_id: str = ""
    # May this episode steer later answers? THREE states, deliberately not a bool: None = legacy row, False = quarantined
    # (explicit decision to withhold), True = eligible. Retrieval admits only True.
    usage_eligible: bool | None = None
    # Procedures that actually influenced THIS run, judged from execution, not from the plan. THREE states: None = legacy row
    # (attribution unknown, infer nothing); () = this version ran and no procedure was applied; (ids...) = application observed.
    # Procedural debit uses these ids, so a looser meaning would turn feedback into misattribution.
    used_procedure_ids: tuple[str, ...] | None = None
    # What the synthesizer declared about reaching the goal, verbatim. Stored because the marker is stripped before verification
    # and never reaches `full_answer`. None = no declaration (legacy row, no marker, or no synthesis).
    declared_completion: CompletionDeclaration | None = None
    # The verdict, ASSEMBLED AT BANKING and frozen: procedural feedback is applied once under the rule in force then, and recomputing
    # would reclassify episodes whose credit or debit is already spent. None = never classified; readers use `effective_completion`.
    completion_state: CompletionState | None = None
    # Defect signals this run raised about ITSELF. THREE states as above: None = row predates this axis; () = ran and no sensor fired;
    # (names...) = fired.
    # 
    # Authority is PER SIGNAL, never blanket: `obligation_silently_missing` lowers `achieved` to `partially_achieved` at banking (and so
    # withholds procedure credit); terminal `citation_fabricated` blocks delivery; `reasoning_action_mismatch` stays observational.
    # A new member gets no power until it is named in the verdict rule table (the operator's call).
    defect_signals: tuple[str, ...] | None = None
    # The authoritative fact that displaced this run's own claim, if any (None = the claim stood). `declared_completion` is NEVER edited
    # to match: both survive so the disagreement is readable as its own fact.
    completion_override: str | None = None
    #: `path<TAB>sha256` of each file behind the answer, taken at banking; the
    #: replay gate serves the answer only while every hash still matches (17.11).
    input_digests: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: new_id("ep"))
    created_at: str = field(default_factory=_now_iso)
    verification_subject: Literal["answer", "rejected_draft"] = "answer"

    @property
    def draft_quality_score(self) -> float | None:
        """Keep the rejected draft's measurement separate from the final answer."""
        if self.verification_subject != "rejected_draft":
            return None
        return _compute_quality_score(
            self.verified_chunks, self.unverified_chunks, self.weak_chunks,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            # Omit-when-empty: a legacy row stays byte-identical.
            **({} if not self.trace_id else {"trace_id": self.trace_id}),
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
            **({} if self.verification_subject == "answer"
               else {"verification_subject": self.verification_subject}),
            "replan_exhausted": self.replan_exhausted,
            "tags": list(self.tags),
            "created_at": self.created_at,
            # Omit-when-None, not an explicit null: an absent key honestly encodes "this row predates the axis" and keeps a legacy row byte-identical.
            **({} if self.declared_completion is None
               else {"declared_completion": self.declared_completion}),
            **({} if self.completion_state is None
               else {"completion_state": self.completion_state}),
            **({} if self.defect_signals is None
               else {"defect_signals": list(self.defect_signals)}),
            **({} if self.completion_override is None
               else {"completion_override": self.completion_override}),
            # Basis for the third eligibility axis. Not derivable from counters (unlike answer_quality_score), hence stored;
            # without it a usage_eligible=False verdict could not be audited.
            **({} if self.relevance_score is None
               else {"relevance_score": self.relevance_score}),
            **({} if not self.input_digests else {"input_digests": list(self.input_digests)}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodeRecord:
        return cls(
            id=str(data.get("id") or _legacy_id("ep", data)),
            task_id=str(data.get("task_id") or ""),
            run_id=str(data.get("run_id") or ""),
            trace_id=str(data.get("trace_id") or ""),
            # `.get` WITHOUT a default: an absent key must stay None (legacy_unclassified), never collapse into False (quarantined).
            usage_eligible=_stored_flag(data.get("usage_eligible")),
            used_procedure_ids=(
                None
                if data.get("used_procedure_ids") is None
                else _str_items(data["used_procedure_ids"])
            ),
            # Read back as stored, never re-assembled: a legacy row stays None even with an abort tag or `replan_exhausted`, since reconstructing
            # a verdict would hand one to episodes whose feedback was applied without it. An unrecognised token is refused.
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
            # Absent key stays None (legacy row), an empty list stays (): "never looked" differs from "looked and saw nothing".
            defect_signals=(
                None
                if data.get("defect_signals") is None
                else _str_items(data["defect_signals"])
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
            tools_used=_str_items(data.get("tools_used")),
            source_labels=_str_items(data.get("source_labels")),
            verified_chunks=max(0, int(data.get("verified_chunks") or 0)),
            unverified_chunks=max(0, int(data.get("unverified_chunks") or 0)),
            weak_chunks=max(0, int(data.get("weak_chunks") or 0)),
            replan_exhausted=bool(_stored_flag(data.get("replan_exhausted", False))),
            verification_subject=(
                "rejected_draft" if data.get("verification_subject") == "rejected_draft"
                else "answer"
            ),
            answer_quality_score=(
                None if data.get("verification_subject") == "rejected_draft"
                else _compute_quality_score(
                    max(0, int(data.get("verified_chunks") or 0)),
                    max(0, int(data.get("unverified_chunks") or 0)),
                    max(0, int(data.get("weak_chunks") or 0)),
                )
            ),
            tags=_str_items(data.get("tags")),
            full_answer=str(data.get("full_answer") or ""),
            created_at=str(data.get("created_at") or _UNKNOWN_TIME),
            relevance_score=_stored_relevance(data.get("relevance_score")),
            input_digests=_str_items(data.get("input_digests")),
        )


@dataclass(frozen=True)
class ProcedureRecord:
    """A reusable workflow distilled from successful episodes."""

    name: str
    workflow_key: str
    trigger_tags: tuple[str, ...]
    steps: tuple[str, ...]
    # One factual line per contributing episode (what was asked, what it worked on, how the claims held), accumulated and parallel to
    # `source_episode_ids`: a `workflow_key` pools unrelated runs, so one summary line would describe one situation and claim another's
    # successes. Nothing is inferred; an empty tuple means the run left no honest material.
    lessons: tuple[str, ...] = ()
    source_episode_ids: tuple[str, ...] = ()
    #: Вопросы, из которых процедура сделана — НЕЗАВИСИМО от эпизодов, которые
    #: гигиена вытесняет, и НЕ участвуя в подборе. Зачем: MIR-165.
    source_questions: tuple[str, ...] = ()
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
            "source_questions": list(self.source_questions),
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
            id=str(data.get("id") or _legacy_id("proc", data)),
            name=str(data.get("name") or "workflow"),
            workflow_key=str(data.get("workflow_key") or ""),
            # Capped on the way IN as well as out: a record from an earlier version or hand-edited would otherwise keep unbounded fields,
            # which are spent on every later run because the record is injected into planner prompts.
            trigger_tags=tuple(dict.fromkeys(
                _str_items(data.get("trigger_tags"))
            ))[:_MAX_TRIGGER_TAGS],
            steps=_str_items(data.get("steps")),
            lessons=_capped_lessons(_str_items(data.get("lessons"))),
            source_episode_ids=_capped_episode_ids(
                str(x) for x in data.get("source_episode_ids") or ()
            ),
            source_questions=_capped_source_questions(
                str(x) for x in data.get("source_questions") or ()
            ),
            success_count=max(0, int(data.get("success_count") or 0)),
            failure_count=max(0, int(data.get("failure_count") or 0)),
            confidence=_stored_confidence(
                data.get("confidence"),
                max(0, int(data.get("success_count") or 0)),
                max(0, int(data.get("failure_count") or 0)),
            ),
            status=_procedure_status(str(data.get("status") or "active")),
            created_at=str(data.get("created_at") or _UNKNOWN_TIME),
            updated_at=str(data.get("updated_at") or data.get("created_at") or _UNKNOWN_TIME),
        )

    def with_outcome(self, episode: EpisodeRecord, verdict: str) -> ProcedureRecord:
        """Apply one observation, recomputing every derived value together."""
        success_count = self.success_count + (1 if verdict == "success" else 0)
        failure_count = self.failure_count + (1 if verdict == "failure" else 0)
        confidence = _smoothed_confidence(success_count, failure_count)
        return replace(
            self,
            source_episode_ids=_capped_episode_ids([*self.source_episode_ids, episode.id]),
            source_questions=_capped_source_questions(
                [*self.source_questions, episode.question]
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
        """Fold an episode's PROVENANCE into this procedure without crediting it.

                The cap applies on EVERY return: an uncredited fold-in must repair an oversized older record, not carry it forward.
        """
        episode_ids = _capped_episode_ids([*self.source_episode_ids, episode.id])
        lesson = lesson_from_episode(episode)
        lessons = _capped_lessons(self.lessons)
        if lesson and procedure_credit_allowed(episode):
            lessons = _capped_lessons([*self.lessons, lesson])
        return replace(
            self,
            source_episode_ids=episode_ids,
            source_questions=_capped_source_questions(
                [*self.source_questions, episode.question]
            ),
            lessons=lessons,
            updated_at=_now_iso(),
        )

    # No counter-moving fold-in exists: counters move only through the causal `apply_episode_feedback` -> `with_outcome` path.


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
    # Defaulted, so reports written before the `candidate` status round-trip unchanged instead of failing to load.
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

        Same contract as `RetrievalSelection` on the persistent side: reasons are reported by the deciding component and counted by
        reason, never per record; an absent reason means zero.
    """

    episodes: list[EpisodeRecord]
    rejected_by: dict[str, int]


@dataclass(frozen=True)
class ProcedureSearchResult:
    """What a procedure search offered, and why the rest of the store did not.

        Same contract as `EpisodeSearchResult`. `excluded_candidate` is the maturity gate (a candidate is not offered until a second
        success promotes it), reported so candidate-vs-no-match is visible.
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

                Bounded idempotency: it holds only while the episode is inside the FIFO window (`max_episodes`); once evicted, its id is
                writable again. Deduplication for a run and its immediate retries, not a permanent claim.
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
        """Evict oldest non-protected episodes when over *max_episodes*; returns the number removed.

                Protected episodes (any tag in PROTECTED_TAGS) go last, not never: once unprotected rows are gone, the OLDEST protected
                ones are evicted too, so the cap holds.
        """
        if self.max_episodes <= 0:
            return 0
        rows = read_state_jsonl_unlocked(self.path)
        if len(rows) <= self.max_episodes:
            return 0
        # Pair each parsed episode with its ORIGINAL row: kept rows are written back as they were (unknown fields, stored id and date
        # intact), and an unparseable row is kept untouched rather than deleted. Only readable rows are evicted.
        parsed: list[tuple[int, EpisodeRecord]] = []
        # The row appended by this very call (always last) is never a candidate: with many unreadable rows the cut could evict
        # the episode `save` is about to report as stored.
        for index, row in enumerate(rows[:-1]):
            try:
                parsed.append((index, EpisodeRecord.from_dict(row)))
            except (TypeError, ValueError, OverflowError):
                continue
        protected = [p for p in parsed if self.PROTECTED_TAGS & set(p[1].tags)]
        evictable = [p for p in parsed if not (self.PROTECTED_TAGS & set(p[1].tags))]
        to_remove = len(rows) - self.max_episodes
        to_remove = min(to_remove, len(parsed))
        if to_remove <= 0:
            return 0
        # Sort by age ascending so oldest are removed first; protected only
        # after every evictable row is gone.
        evictable.sort(key=lambda p: p[1].created_at)
        protected.sort(key=lambda p: p[1].created_at)
        from_protected = max(0, to_remove - len(evictable))
        removed = {i for i, _ in evictable[:to_remove]} | {i for i, _ in protected[:from_protected]}
        rewrite_state_jsonl_unlocked(
            self.path, [row for i, row in enumerate(rows) if i not in removed]
        )
        return len(removed)

    def load(self) -> list[EpisodeRecord]:
        with state_file_lock(self.path):
            rows = read_state_jsonl_unlocked(self.path)
        out: list[EpisodeRecord] = []
        for row in rows:
            try:
                out.append(EpisodeRecord.from_dict(row))
            except (TypeError, ValueError, OverflowError):
                continue
        return out

    def count(self) -> int:
        return len(self.load())

    def search(self, query: str, *, limit: int = 3) -> list[EpisodeRecord]:
        """The matching episodes. Delegates so there is one decision, not two."""
        return self.search_with_report(query, limit=limit).episodes

    @staticmethod
    def _signal_standing(ep: EpisodeRecord) -> int:
        """2 = confirmed, 1 = no evidence at all, 0 = measured and did not hold.

                Three states, not two: "not measured" is ignorance, and equating it with failure would pass ignorance off as a verdict.
        """
        if (ep.verified_chunks or 0) > 0:
            return 2
        return 0 if (ep.unverified_chunks or 0) + (ep.weak_chunks or 0) else 1

    def _rank(self, score: int, ep: EpisodeRecord) -> tuple:
        """Ranking: relevance -> label -> verification signal -> recency.

                The label grants the right to SURVIVE and to go first AT EQUAL relevance, not to override relevance
                (the old +50 bonus exceeded any overlap).
        """
        return (
            score,
            1 if self.PROTECTED_TAGS & set(ep.tags) else 0,
            self._signal_standing(ep),
            # Admitting a partial record does not equate it with a completed one: otherwise a fresh partial would beat a full one on recency alone.
            1 if effective_completion(ep) == "achieved" else 0,
            ep.created_at,
        )

    def search_with_report(
        self,
        query: str,
        *,
        limit: int = 3,
        admit: Callable[[EpisodeRecord], str | None] | None = None,
    ) -> EpisodeSearchResult:
        """`search`, plus why the rest of the store did not come back.

                A caller that sees only survivors cannot report the drops made in here (`episodes_selected=0, rejected_by={}` was reachable on a
                store of 200 episodes). `admit` returns a rejection reason or None and runs BEFORE the limit, so records the caller would
                refuse cannot take the slots of one it would accept.
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
        refused: dict[str, int] = {}
        q_content = q_tokens - STOPWORDS
        for ep in episodes:
            haystack = " ".join([ep.goal, ep.question, ep.summary, " ".join(ep.tags)])
            hay_tokens = _tokens(haystack)
            score = len(q_tokens & hay_tokens)
            # A match made of function words is not a match. Scoring still counts every shared token; only ELIGIBILITY requires at least one
            # discriminating word (filler alone retrieved 17 of 34 procedures and scored a launch and a deletion as one question).
            if not (score and (q_content & hay_tokens)):
                no_overlap += 1
                continue
            try:
                reason = admit(ep) if admit is not None else None
            except Exception:  # noqa: BLE001 — counted below, never silent
                # One record the gate cannot judge is refused and counted; it must not abort retrieval for the whole turn.
                reason = "admit_error"
            if reason:
                refused[reason] = refused.get(reason, 0) + 1
                continue
            scored.append((score, ep))
        scored.sort(key=lambda item: self._rank(*item), reverse=True)
        selected = [ep for _score, ep in scored[:limit]]
        rejected_by = {
            k: v for k, v in (
                ("no_overlap", no_overlap),
                *refused.items(),
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
        self,
        query: str,
        *,
        threshold: float = 0.35,
        admit: Callable[[EpisodeRecord], bool] | None = None,
    ) -> tuple[EpisodeRecord | None, float]:
        """Return the episode whose *question* has the highest Jaccard similarity to *query*, and the score; ``(None, 0.0)`` when none reaches *threshold*.

                Only the stored ``question`` field is compared, so the signal is "did the user ask THIS before?", not "is this topic familiar?".
                A candidate with a low ``answer_quality_score`` gets the threshold lowered by 0.10. ``admit`` (True = accept) runs BEFORE the best is
                chosen, so a refused winner cannot hide an admissible runner-up.
        """
        # The frame is not the question: similarity is computed over DISCRIMINATING words only (a shared frame such as "I want to X, what
        # should I do?" alone scored the threshold, so a launch was annotated as a repeat of a deletion). A question made entirely of filler
        # matches nothing.
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
            if score > best_score and (admit is None or admit(ep)):
                best_score = score
                best_ep = ep
        if best_ep is None:
            return None, 0.0
        # Lower the threshold when the best candidate was a low-quality or unmeasured answer: a re-ask hint is an offer to go deeper, not a verdict.
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
        keep_ids: frozenset[str] = frozenset(),
    ) -> list[str]:
        """Remove old, low-quality, non-protected episodes; returns the evicted IDs.

                Wraps :func:`core.episodic_hygiene.prune_stale_episodes`. ``keep_ids`` (procedure-credit anchor episodes) are never handed to cleanup.
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
            keep_ids=keep_ids,
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
            except (TypeError, ValueError, OverflowError):
                continue
        return out

    def rewrite(self, procedures: list[ProcedureRecord]) -> None:
        """The given list becomes the READABLE set; each record is merged over its original row (unknown keys kept), unreadable rows are kept untouched.

                Only when you own the whole store (tests, fresh files): a list loaded earlier silently undoes every write made since.
                Use `update_procedures` for read-modify-write.
        """
        with state_file_lock(self.path):
            self._replace_readable_unlocked(self._rows_unlocked(), procedures)

    def update_procedures(
        self, fn: Callable[[list[ProcedureRecord]], list[ProcedureRecord]]
    ) -> list[ProcedureRecord]:
        """Locked read-modify-write: `fn` gets the CURRENT readable list and returns the new one; unreadable rows and unknown keys survive;
                the same objects back = no write.
        """
        with state_file_lock(self.path):
            rows = self._rows_unlocked()
            current = [p for _row, p in rows if p is not None]
            new = list(fn(list(current)))
            if len(new) != len(current) or any(a is not b for a, b in zip(new, current, strict=True)):
                self._replace_readable_unlocked(rows, new)
            return new

    def _replace_readable_unlocked(
        self,
        rows: list[tuple[dict[str, Any], ProcedureRecord | None]],
        procedures: list[ProcedureRecord],
    ) -> None:
        """`procedures` over their original rows, then the unreadable rows as read."""
        original: dict[str, dict[str, Any]] = {}
        for row, proc in rows:
            if proc is not None:
                original.setdefault(proc.id, row)
        payloads = [{**original.get(p.id, {}), **p.to_dict()} for p in procedures]
        payloads.extend(row for row, proc in rows if proc is None)
        rewrite_state_jsonl_unlocked(self.path, payloads)

    def _rows_unlocked(self) -> list[tuple[dict[str, Any], ProcedureRecord | None]]:
        """Every stored row with its parsed record, or None when it cannot be read."""
        out: list[tuple[dict[str, Any], ProcedureRecord | None]] = []
        for row in read_state_jsonl_unlocked(self.path):
            try:
                out.append((row, ProcedureRecord.from_dict(row)))
            except (TypeError, ValueError, OverflowError):
                out.append((row, None))
        return out

    def _write_rows_unlocked(
        self,
        rows: list[tuple[dict[str, Any], ProcedureRecord | None]],
        changed: dict[str, ProcedureRecord],
        added: list[ProcedureRecord] = (),  # type: ignore[assignment]
    ) -> None:
        """Rewrite: changed records over their original row (unknown keys kept), every other row exactly as read, unreadable ones included."""
        payloads = [
            {**row, **changed[proc.id].to_dict()} if proc is not None and proc.id in changed else row
            for row, proc in rows
        ]
        payloads.extend(p.to_dict() for p in added)
        rewrite_state_jsonl_unlocked(self.path, payloads)

    def count(self) -> int:
        return len(self.load())

    def apply_episode_feedback(
        self, episode: EpisodeRecord, *, allow_credit: bool = True
    ) -> dict:
        """Feed one run's outcome back to the procedures it actually used.

                `allow_credit=False` withholds the POSITIVE direction only, for a cycle whose verifier threw (`verified=0, unverified=0` falls
                through to `success`, so an unmeasured run would credit a procedure like a verified one). The negative direction is untouched:
                a debit comes from a structural failure the verifier had no part in. The suppression is reported, not disguised as an absent verdict.
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
        # A credit withheld only for lack of a verified fact is named, so it does not read like a run that simply did not succeed.
        if (
            verdict == "none"
            and episode.verified_chunks == 0
            and procedure_credit_allowed(replace(episode, verified_chunks=1))
        ):
            report["credit_withheld"] = "no_verified_evidence"
        if not used:
            return report
        if report["verdict"] == "none":
            report["skipped"] = len(dict.fromkeys(used))
            return report

        with state_file_lock(self.path):
            rows = self._rows_unlocked()
            by_id = {p.id: p for _row, p in rows if p is not None}
            changed: dict[str, ProcedureRecord] = {}
            for pid in dict.fromkeys(used):        # collapse duplicates first
                target = by_id.get(pid)
                if target is None:
                    report["orphaned"] += 1
                    continue
                if episode.id in target.source_episode_ids:
                    report["already_applied"] += 1
                    continue
                changed[pid] = target.with_outcome(episode, report["verdict"])
                report["applied"] += 1
            if changed:
                self._write_rows_unlocked(rows, changed)
        return report

    def recompute_legacy_confidence(
        self, *, dry_run: bool = True, limit: int | None = None
    ) -> dict:
        """Restore `confidence == _smoothed_confidence(success, failure)`.

                Touches only derived values: `confidence`, and `status` with it (decided by `_procedure_status_for` from the counters),
                since a stale status would trade one broken invariant for another. The repair only lowers or keeps standing (see `_repaired_status`).
        """
        # One lock for the read AND the write.
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
                status = _repaired_status(row.get("status"), success, failure, expected)
                if (
                    isinstance(stored, (int, float))
                    and round(float(stored), 3) == expected
                    and row.get("status") == status
                ):
                    report["already_consistent"] += 1
                    continue
                if not row.get("id"):
                    # Pin the legacy id before the row changes, or the content
                    # hash would give it a different id after the rewrite.
                    row["id"] = _legacy_id("proc", row)

                report["eligible"] += 1
                if limit is not None and report["corrected"] >= limit:
                    # Bounded and resumable: the remainder is picked up next run.
                    report["limit_reached"] = True
                    report["skipped"] += 1
                    continue

                report["corrected"] += 1
                if not dry_run:
                    row["confidence"] = expected
                    # The field had a THIRD authority: this line knew only `active`/`needs_review` and never produced `candidate`,
                    # so a pass over newborns would rename them all.
                    row["status"] = status
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
                rewrite_state_jsonl_unlocked(self.path, rows)
            return report

    def upsert_from_episode(self, episode: EpisodeRecord) -> tuple[ProcedureRecord | None, bool]:
        candidate = procedure_from_episode(episode)
        if candidate is None:
            return None, False
        # Read and write under ONE lock: two separate locks let a write made in between (a credit from feedback) be overwritten.
        with state_file_lock(self.path):
            rows = self._rows_unlocked()
            changed: dict[str, ProcedureRecord] = {}
            updated: ProcedureRecord | None = None
            for _row, proc in rows:
                if proc is not None and proc.workflow_key == candidate.workflow_key:
                    # Merge provenance only — NO credit for a tool-set match.
                    # Promotion happens solely via `used_procedure_ids`.
                    updated = proc.merged_from_episode(episode)
                    changed[proc.id] = updated
            created = updated is None
            if updated is None and any(
                proc is None and row.get("workflow_key") == candidate.workflow_key
                for row, proc in rows
            ):
                # The workflow already has a row we cannot read. A fresh twin
                # beside it would be a duplicate born on every run; leave the
                # row for repair and report "not stored" (proc None).
                return None, False
            added: list[ProcedureRecord] = []
            if updated is None:
                # A brand-new candidate records its creating episode's PROVENANCE but earns no credit: it was not USED by the run that produced it,
                # so its counters stay at birth (zero).
                updated = candidate.merged_from_episode(episode)
                added.append(updated)
            self._write_rows_unlocked(rows, changed, added)
        return updated, created

    def search(self, query: str, *, limit: int = 3) -> list[ProcedureRecord]:
        """Procedures to offer the planner; thin wrapper over the reporting form so the one production caller can log why the rest were dropped."""
        return self.search_with_report(query, limit=limit).procedures

    def search_with_report(
        self, query: str, *, limit: int = 3, salience: TokenSalience = FLAT
    ) -> ProcedureSearchResult:
        """Same as `search`, but also reports why the store's other procedures did not surface; symmetric with `EpisodicMemoryStore.search_with_report`."""
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
            # A candidate is SHOWN: the maturity gate conflated visibility with credit and locked the circle
            # (see docs/CODE_NOTES.md, "The method that could not survive the turn").
            if proc.status in {"obsolete", "needs_review"}:
                excluded_retired += 1
                continue
            haystack = " ".join([proc.name, " ".join(proc.trigger_tags), " ".join(proc.steps)])
            # Weighted, not counted: three function words must not outweigh one signal name. Without a corpus the weight is flat and the count as before.
            hay_tokens = _tokens(haystack)
            score = salience.overlap(q_tokens, hay_tokens)
            if score and (q_content & hay_tokens):
                scored.append((score, 0 if proc.status == "candidate" else 1, proc))
            else:
                no_overlap += 1
        # Relevance first, maturity only at EQUAL relevance: "unconfirmed does not displace confirmed" stays where maturity lives, but over topic it cost accuracy.
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
            except (TypeError, ValueError, OverflowError):
                continue
        return out

    def count(self) -> int:
        return len(self.load())


def procedure_credit_allowed(episode: EpisodeRecord) -> bool:
    """May this run raise a procedure's standing?

        `completion_state` is read FROZEN through the shared accessor; never recomputed, never taken from `declared_completion`.
    """
    # Third axis, shared with the episode's usage eligibility: one predicate for both boundaries so they cannot diverge.
    if _answer_disqualified(episode):
        return False
    # Memory echo (`memory:`) and an off-topic answer do not raise a method: the same axes that refuse eligibility.
    if _lesson_provenance_disqualified(episode):
        return False
    # At least one verified fact: without it "achieved" is only the run's own word and the method would be raised on self-praise.
    return bool(
        effective_completion(episode) == "achieved"
        and episode.outcome == "success"
        and episode.tools_used
        and episode.verified_chunks > 0
    )


def procedure_debit_allowed(episode: EpisodeRecord) -> bool:
    """May this run lower a procedure's standing?

        Deliberately narrow: only a failure the RUN ITSELF demonstrates (an exhausted replan: the loop gave up after trying, a fact about
        execution the procedure took part in). Everything else is neutral: a DECLARED `failed`, `cancelled`, `blocked` / `refused` /
        `partially_achieved` (the procedure may have executed perfectly), `unknown` / legacy `None`, and an ABORTED run (no
        `used_procedure_ids`, so no causal link to debit through).

        The frozen state is authoritative and comes first: `replan_exhausted` NARROWS an existing `failed`, never creates one.
        Derived `aborted:*` tags are never consulted: a value computed from another value must not authorise a debit.
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
    executed = list(executed_tools)

    def _completed_at(workflow: list[str]) -> int | None:
        """Index where every step of the workflow had run AS MANY TIMES as the workflow names it, or None. Mere membership let a one-time call
                count as applying a workflow that needs it twice. Order is NOT required: the key is built by `episode_tools` (last round's plan
                first), so it need not match the execution order of a multi-round run.
        """
        need: dict[str, int] = {}
        for tool in workflow:
            need[tool] = need.get(tool, 0) + 1
        for index, tool in enumerate(executed):
            if tool in need:
                need[tool] -= 1
                if need[tool] == 0:
                    del need[tool]
                if not need:
                    return index
        return None

    ran: list[tuple[int, str]] = []
    seen: set[str] = set()
    for proc in selected:
        if proc.id in seen:
            continue
        workflow = [t for t in proc.workflow_key.removeprefix("tools:").split("->") if t]
        if not workflow:
            continue
        done = _completed_at(workflow)
        if done is not None:
            ran.append((done, proc.id))
            seen.add(proc.id)
    # Order by the point each workflow completed, so the record reflects the
    # sequence of actual use rather than the order retrieval happened to return.
    return tuple(pid for _done, pid in sorted(ran, key=lambda item: item[0]))


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

        An authoritative signal outranks the run's own self-assessment in the operational outcome without destroying it. Three things are
        stored: what the answer claimed (`EpisodeRecord.declared_completion`, untouched), what holds (`state`), and, only when they diverge,
        which fact displaced the claim (`overridden_by`).
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
    citation_integrity_violation: bool = False,
    checklist_unmet: bool = False,
) -> CompletionVerdict:
    """The single rule table; :func:`assemble_completion_state` delegates here.

        `obligation_unmet` is authoritative in one direction only: it can lower a claim of `achieved` to `partially_achieved`, never raise
        anything. A run that already reported `blocked` or `failed` is not made worse by it.
    """
    if aborted_reason == "cancelled":
        return _displaced("cancelled", declared, "cancelled")
    if aborted_reason:
        return _displaced("failed", declared, "aborted")
    if replan_exhausted:
        return _displaced("failed", declared, "replan_exhausted")
    if citation_integrity_violation and declared not in {"failed", "cancelled"}:
        return _displaced("blocked", declared, "citation_integrity")
    if obligation_unmet and declared == "achieved":
        return _displaced(
            "partially_achieved", declared, "obligation_silently_missing"
        )
    # Same one-sided authority, different ground: `obligation_unmet` is a duty left unmet; here a part of the operator's contract could not
    # be represented at all. Refusing to CERTIFY is not asserting failure: `partially_achieved` means "not everything asked was done".
    if user_contract_partial and declared == "achieved":
        return _displaced(
            "partially_achieved", declared, "user_contract_unrepresented"
        )
    # Task checklist (core/request_checklist.py): the judge answered "no" to an important human requirement ("no more than 8 sentences",
    # "VERDICT line at the end"). Same one-sided authority: lowers, never raises.
    if checklist_unmet and declared == "achieved":
        return _displaced("partially_achieved", declared, "checklist_unmet")
    # The answer-safety check raised, so the run delivered a safe refusal instead of its draft: work happened and the honest outcome reached
    # the user, but the process was defective. `partially_achieved` also withholds procedure credit and eligibility.
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

        `None` on the record means never classified (written before the axis existed); readers get `unknown`, fail-closed at every gate:
        such an episode steers nothing, credits nothing and is not replayed. A stored `None` stays distinct from a stored "unknown".
    """
    state = episode.completion_state
    return state if state in _COMPLETION_STATES else "unknown"  # type: ignore[return-value]


#: Сигналы дефекта, при которых ответ НЕЛЬЗЯ обращать в опыт — ни как эпизод,
#: пригодный к использованию, ни как заслугу процедуры. Список ОДИН на оба
#: рубежа: порознь эти предикаты уже расходились. Сюда попадает только то,
#: что говорит о ЛОЖНОСТИ ответа, и растёт он по доказанному вреду, не по
#: подозрению.
DISQUALIFYING_DEFECT_SIGNALS: frozenset[str] = frozenset(
    {"self_contradiction", "content_refuted", "citation_fabricated", "citation_excised"}
)


def _answer_disqualified(episode: EpisodeRecord) -> bool:
    """Whether this answer refuted itself: one question for both boundaries."""
    return bool(
        DISQUALIFYING_DEFECT_SIGNALS & set(episode.defect_signals or ())
    )


def _lesson_provenance_disqualified(episode: EpisodeRecord) -> bool:
    """The two axes a `lesson` may NOT waive: its sources and its subject.

        Kept beside `_answer_disqualified`: both ask whether the record is admissible at all, unlike the outcome axes, which ask how the run went.
    """
    if any(str(label).startswith("memory:") for label in episode.source_labels):
        return True
    from core.verification_summary import _LOW_RELEVANCE

    return (
        episode.relevance_score is not None
        and episode.relevance_score < _LOW_RELEVANCE
    )


#: Завершённости, при которых запись вообще может править работой. Частичная
#: здесь потому, что подтверждение всё равно требуется ниже: послабление даёт
#: СИГНАЛ, а не слово «частично». Провал, блокировка и `unknown` остаются
#: снаружи — незнание не суждение. Замер: MIR-169.


def decide_usage_eligibility(episode: EpisodeRecord) -> bool:
    """Decide whether a freshly banked episode may steer later answers.

        Admitted only when ALL hold: `completion_state == "achieved"` (read frozen, never re-derived, never from the declaration);
        `outcome == "success"`; `verified_chunks > 0`; and not a replay. Exception: a curated `lesson` is admitted whatever its outcome.
        Always returns a bool (`None` belongs to legacy rows). No threshold constant on purpose: every rule reads a fact the verifier measured.
    """
    # BEFORE all other axes, including the lesson exemption: an answer that refuted itself never becomes experience on any ground,
    # since it passes EVERY check below (verification measures link resolvability, not the truth of the referent).
    if _answer_disqualified(episode):
        return False
    if "lesson" in episode.tags:
        # The exemption waives what it promised to waive and no more. A legitimate failure costs an episode its outcome, completion and verified
        # chunks, and remembering that failure as a warning is the point of the tag. It does NOT excuse two axes unrelated to failing: a `memory:`
        # source is memory citing itself (an echo is not a second witness and is the amplification step of a memory-poisoning attack), and an
        # off-topic record is off-topic whether or not it failed. No live lesson is affected: this is prophylaxis on the attack path.
        return not _lesson_provenance_disqualified(episode)
    if episode.outcome != "success":
        return False
    # The second axis: `outcome` says the claims held up, which a blocked non-answer can satisfy perfectly. Both must agree, neither substitutes
    # for the other, and this one only subtracts permission. Read from the FROZEN state through the shared accessor.
    completion = effective_completion(episode)
    if completion not in _ADMISSIBLE_COMPLETIONS:
        return False
    # "Partial" has TWO origins and the differentiator is in the record: the run's own honest assessment versus a forced downgrade when an
    # authoritative signal displaced its claim. The second was demoted by the operator's rule; admitting it as honest partiality would void it.
    if completion != "achieved" and episode.completion_override is not None:
        return False
    if any(str(label).startswith("memory:") for label in episode.source_labels):
        return False
    # Third axis: the first two ask whether the claims held, this asks whether they answered THIS question. The threshold is shared with the operator warning.
    from core.verification_summary import _LOW_RELEVANCE

    if episode.relevance_score is not None and episode.relevance_score < _LOW_RELEVANCE:
        return False
    return episode.verified_chunks > 0


def is_usage_eligible(episode: EpisodeRecord) -> bool:
    """May this episode steer a later answer?

        Only an EXPLICIT `True` admits it; both `None` (legacy_unclassified) and `False` (quarantined) are refused (fail-closed).
    """
    return episode.usage_eligible is True


def admit_for_storage(episode: EpisodeRecord) -> EpisodeRecord:
    """Resolve the admission verdict for an episode about to be written.

        ``None`` (no one has decided yet) is the only state resolved; an explicit ``True``/``False`` is a decision already taken and passes
        through untouched (that is how an aborted run stays quarantined). Idempotent, and applied on **write only**: a ``None`` read back
        from disk is a row predating the field, and re-deciding it would rewrite history with today's rule.
    """
    if episode.usage_eligible is None:
        episode = replace(
            episode, usage_eligible=decide_usage_eligibility(episode)
        )
    # The completion axis, same boundary: every NEW record carries an explicit verdict (a writer that cannot classify writes ``"unknown"``).
    # Absence is legal only when READING rows that predate the axis. Write-only: a `None` read back from disk stays `None`.
    if episode.completion_state is None:
        episode = replace(episode, completion_state="unknown")
    return episode


def _derive_episode_outcome(
    *,
    aborted_reason: str,
    replan_exhausted: bool,
    declared_completion: str | None,
    verified: int,
    unverified: int,
    weak: int,
    answer_withheld: bool = False,
) -> EpisodeOutcome:
    """The evidence axis, decided in one place. The ORDER is the contract:

    1. the run did not finish — no chunk count can express that;
    2. replanning was exhausted — same;
    3. the answer was withheld for invalid citations;
    4. the run SAID it did not deliver — an admission, not a verdict;
    5. otherwise the counters judge the support the answer had.
    """
    if aborted_reason:
        # The run did not finish: decided before the counters and never overridden into a success by them.
        return "failed"
    if replan_exhausted:
        return "failed"
    if answer_withheld:
        return "failed"
    if declared_completion in _NON_DELIVERY_DECLARATIONS:
        # A well-cited non-delivery used to count as a well-cited success (outcome came from chunk counts alone), so memory recorded a success for
        # an answer that said the work was not done. `usage_eligible=False` does not repair that: the row still READS as a success.
        return "failed" if declared_completion == "failed" else "partial"
    if unverified > verified:
        # A relative-majority test (mirrors the `weak >= verified` guard below), not a magic threshold: one lucky verified chunk must not
        # immunise an answer with many more unverified ones. verified=0/unverified=0 is untouched: general knowledge stays `success`.
        return "partial"
    if weak > 0 and weak >= verified:
        # The answer leans at least as much on support the verifier could not confirm as on verified evidence: not a clean success, so it must
        # not graduate to a reusable procedure or bank a 1.0 quality score.
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
    trace_id: str = "",
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
    cancellation, a timeout). Terminal rejection also outranks chunk counts:
    the draft's measurements remain diagnostic, not a score for the refusal.
    """
    verified = max(0, int(verified_chunks))
    unverified = max(0, int(unverified_chunks))
    weak = max(0, int(weak_chunks))
    signals = (
        None if defect_signals is None
        else tuple(dict.fromkeys(
            cleaned for s in defect_signals if (cleaned := str(s).strip())
        ))
    )
    answer_withheld = "citation_fabricated" in (signals or ())
    outcome = _derive_episode_outcome(
        aborted_reason=aborted_reason,
        replan_exhausted=replan_exhausted,
        declared_completion=declared_completion,
        verified=verified,
        unverified=unverified,
        weak=weak,
        answer_withheld=answer_withheld,
    )
    tools = tuple(str(t) for t in tools_used if str(t).strip())
    labels = tuple(str(label) for label in source_labels if str(label).strip())
    tags = _episode_tags(tools=tools, outcome=outcome, labels=labels)
    if aborted_reason:
        tags = (*tags, "aborted", f"aborted:{aborted_reason}")
    # A run-derived id makes duplicate detection possible without a ledger: the store can recognise "this attempt was already banked".
    # Runs without an id keep the random default.
    episode_id = episode_id_for_run(run_id) if run_id else new_id("ep")
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
        citation_integrity_violation=answer_withheld,
        checklist_unmet="checklist_unmet" in (signals or ()),
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
        verification_subject="rejected_draft" if answer_withheld else "answer",
        answer_quality_score=(
            None if answer_withheld else _compute_quality_score(verified, unverified, weak)
        ),
        # Same check as on read: NaN or a value outside [0, 1] is "not measured", never a pass of the relevance gate.
        relevance_score=_stored_relevance(relevance_score),
        tags=tags,
        task_id=str(task_id or ""),
        run_id=str(run_id or ""),
        trace_id=str(trace_id or ""),
        # Defaults to None (legacy_unclassified): banking an episode is not itself a verdict that it may steer later answers; the caller decides.
        usage_eligible=usage_eligible,
        used_procedure_ids=used_procedure_ids,
        # Both completion fields are settled here, at banking, and never recomputed. An unrecognised token is dropped, so the declaration column
        # only holds what the parser may produce.
        declared_completion=_checked_declaration(declared_completion, on_audit),
        completion_state=_verdict.state,
        completion_override=_verdict.overridden_by,
        # Order preserved, duplicates dropped: a sensor firing on three attempts is one fault, and a stable order keeps runs byte-comparable.
        # `None` passes through so a caller that cannot collect signals stays distinguishable from one that collected none.
        defect_signals=signals,
        id=episode_id,
    )


# Bounds on the two fields that accumulate across episodes: a common `workflow_key` pools every credited run into one record, so both
# would grow without limit, and the growth is paid again on every later run (record rewritten to JSONL, injected into planner prompts).
# Unbounded tags also destroy retrieval: a record carrying every token matches every query. The most RECENT lessons are kept.
#: Сколько вопросов-происхождений держит одна процедура. Запись уходит в
#: подсказки планировщика, поэтому ограничено всё, что в ней копится.
#: Evidence labels shown in one line, before a "+N more" summary. Shared by the
#: lesson and the step so the two cannot drift apart.


def lesson_from_episode(episode: EpisodeRecord) -> str:
    """One factual line about what this run met and how it went; never inferred."""
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
    # Faults are part of the causal record: a workflow that worked despite an observed fault differs from one that ran clean,
    # and hiding that is how a flawed method gets reused as a good one.
    if episode.defect_signals:
        parts.append("observed: " + ", ".join(episode.defect_signals))
    return "; ".join(parts)


def procedure_from_episode(episode: EpisodeRecord) -> ProcedureRecord | None:
    # Same predicate as the counter and the verdict: a workflow is worth minting only from a run that finished the job.
    if not procedure_credit_allowed(episode):
        return None
    # Left as the tool sequence on purpose: `resolve_used_procedures` parses it back to decide which procedures ran, so the key is
    # load-bearing for attribution. Its known pooling defect is why `lessons` accumulates instead of holding one summary line.
    workflow_key = "tools:" + "->".join(episode.tools_used)
    lesson = lesson_from_episode(episode)
    subject = _clean_text(episode.question, max_chars=60)
    readable_tools = ", ".join(episode.tools_used)
    steps = (
        # The situation first: a step list starting with the tools answers "how" without saying "for what".
        f"Situation: {subject}" if subject else "Situation: (not recorded)",
        *(f"Run tool: {tool}" for tool in episode.tools_used),
        # Bounded by the same helper as the lesson: this string is stored verbatim and scored in retrieval, so an unbounded sweep over many
        # files would put an arbitrarily long line into both.
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
        # Question tokens join the tags so retrieval can match what the workflow was FOR (scoring reads name/tags/steps, and tool names alone
        # score zero against a subject query). Tokenised from the FULL question, never the 60-char display subject.
        trigger_tags=tuple(dict.fromkeys([
            *episode.tools_used, *episode.tags, *sorted(_tokens(episode.question)),
        ]))[:_MAX_TRIGGER_TAGS],
        steps=tuple(steps),
        lessons=(lesson,) if lesson else (),
        source_episode_ids=(),
        # The full question, not the 60-char `subject` from the steps: that one is truncated and lies inside the retrieval haystack,
        # so it cannot witness provenance.
        source_questions=_capped_source_questions((episode.question,)),
        success_count=0,
        failure_count=0,
        confidence=0.5,
        # Born unproven: `upsert_from_episode` folds in the first episode via `merged_from_episode` (provenance and lesson only, no counter or
        # status change). Promotion comes solely from the causal feedback path, so a fresh procedure stays `candidate`.
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
    open_tag, close_tag = "<agent_experience_memory>", "</agent_experience_memory>"
    # Stored text is data: angle brackets are swapped for look-alikes so a remembered answer cannot close the block and speak as an
    # instruction. One entry = one line: a stored id with a newline started its own line inside the block.
    def _safe(text: str) -> str:
        return (
            str(text).replace("<", "‹").replace(">", "›")
            .replace("\r", " ").replace("\n", " ")
        )

    lines = []
    if procedures:
        lines.append("procedures:")
        for proc in procedures:
            lines.append(_safe(
                f"- [{proc.id}] {proc.name}; confidence={proc.confidence}; "
                f"status={proc.status}; steps={' | '.join(proc.steps[:5])}"
            ))
    if episodes:
        lines.append("episodes:")
        for ep in episodes:
            lines.append(_safe(
                f"- [{ep.id}] outcome={ep.outcome}; tools={','.join(ep.tools_used) or 'none'}; "
                f"summary={ep.summary}"
            ))
    body = "\n".join(lines)
    # Cut the BODY, never the closing tag: a block without its end leaves the
    # model unable to tell where memory stops.
    room = max_chars - len(open_tag) - len(close_tag) - 2
    if len(body) > room:
        body = body[: max(0, room - 3)].rstrip() + "..."
    return f"{open_tag}\n{body}\n{close_tag}"


def family_product_warnings(
    selected: list[EpisodeRecord],
    all_episodes: Iterable[EpisodeRecord],
    *,
    max_each: int = 200,
) -> list[str]:
    """Product outcomes of the same runs as the selected success episodes.

        A success episode must not ride into a future hint without the declined sibling from the same tick; the family is joined by
        trace_id. A pure function, so any test bed can judge it.
    """
    traces = {
        ep.trace_id for ep in selected
        if ep.trace_id and ep.outcome == "success"
    }
    if not traces:
        return []
    chosen_ids = {ep.id for ep in selected}
    warnings: list[str] = []
    for cand in all_episodes:
        if (
            cand.trace_id in traces
            and cand.outcome != "success"
            and cand.id not in chosen_ids
        ):
            warnings.append(cand.summary[:max_each])
    return warnings
