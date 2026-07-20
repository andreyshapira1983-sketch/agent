"""Episodic, procedural and consolidation memory for autonomous operation.

This module is intentionally local, deterministic and auditable. It does not
train a model. It stores what happened, turns successful tool workflows into
small reusable procedures, and writes consolidation reports that link episodes
to procedures and surface stale knowledge risks.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from core.ids import new_id
from core.redaction import redact_dlp_text
from core.state_integrity import (
    append_state_jsonl_unlocked,
    read_state_jsonl_unlocked,
    rewrite_state_jsonl_unlocked,
    state_file_lock,
)


EpisodeOutcome = Literal["success", "partial", "failed"]

# Task completion, kept deliberately apart from `EpisodeOutcome` (MIR-057).
# `outcome` answers "were the claims supported"; this answers "was the goal
# reached". A cycle blocked by a truncated evidence budget can be impeccably
# supported and still have answered nothing, which is how a non-answer became
# the first episode the system ever admitted as reusable experience.
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
ProcedureStatus = Literal["active", "needs_review", "obsolete"]


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

    ``weak`` counts claims the verifier could NOT confirm as faithful support —
    sub-agent-asserted, cited-but-unmatched, receipt-missing and topic-only
    chunks. They belong in the denominator, never in the numerator: an answer
    resting on unconfirmed support must not score as if it were verified.

    Returns **None** when there are no chunks at all (a pure general-knowledge
    answer): with an empty denominator the fraction is undefined, not perfect.
    This used to return 1.0 "so general knowledge is not penalised", but the
    top of the scale is not a neutral value — every consumer read it as
    "fully verified" and inverted (MIR-002): groundless episodes outlived
    well-evidenced ones in hygiene, were shielded from pruning, and were less
    likely to trigger a re-ask hint. Consumers must now decide explicitly what
    an unmeasured answer means for them.
    """
    total = verified + unverified + weak
    if total == 0:
        return None
    return round(verified / total, 3)


# Laplace (add-one) smoothing prior for procedure confidence. A procedure is a
# reusable skill distilled from episodes; its confidence must reflect how much
# EVIDENCE backs it, not just the raw success ratio. Without smoothing a single
# successful episode yields success/total = 1/1 = 1.0 — the agent would treat a
# workflow it has seen work exactly once as a certainty. Beta(1,1) smoothing
# makes one success land at (1+1)/(1+2) = 0.667 (active but modest) and lets
# confidence climb toward — but never reach — 1.0 as successes accumulate.
_CONF_PRIOR_SUCCESS: float = 1.0
_CONF_PRIOR_FAILURE: float = 1.0


def _smoothed_confidence(success_count: int, failure_count: int) -> float:
    """Beta(1,1)-smoothed success probability, rounded to 3 dp.

    ``(success + 1) / (success + failure + 2)`` — a single observation is weak
    evidence, so one success stays well below 1.0 and grows asymptotically as
    the workflow keeps succeeding.
    """
    numerator = success_count + _CONF_PRIOR_SUCCESS
    denominator = success_count + failure_count + _CONF_PRIOR_SUCCESS + _CONF_PRIOR_FAILURE
    return round(numerator / denominator, 3)


def _tokens(text: str) -> set[str]:
    normalized = (
        str(text or "")
        .replace("\\", " ")
        .replace("/", " ")
        .replace("_", " ")
        .replace("-", " ")
        .replace(".", " ")
    )
    return {
        token.casefold()
        for token in normalized.split()
        # Keep tokens over 2 chars, OR any token bearing a digit — short numeric
        # / alphanumeric tokens ("17", "23", "v2", "3d") are real match signal
        # that the length floor alone silently dropped (CORE-10). Purely
        # short alphabetic (stopword-like) tokens still fall through.
        if len(token.strip()) > 2 or any(ch.isdigit() for ch in token)
    }


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
    # bool: None = legacy_unclassified (written before this field existed),
    # False = quarantined (an explicit decision to withhold), True = eligible.
    # Collapsing None into False would make a legacy row indistinguishable
    # from a deliberate quarantine. Retrieval admits only True — see
    # `is_usage_eligible`.
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
    # The verdict, ASSEMBLED AT BANKING and frozen. Deliberately stored rather
    # than derived on read: procedural feedback is applied once, under the rule
    # in force at the time, and a state recomputed on every read would silently
    # reclassify episodes whose credit or debit has already been spent.
    # None = never classified (a row written before this field), and stays
    # distinguishable from an explicit verdict exactly as `usage_eligible` does.
    # Readers go through `effective_completion`, which maps None → "unknown".
    completion_state: CompletionState | None = None
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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EpisodeRecord":
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
            "source_episode_ids": list(self.source_episode_ids),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "confidence": self.confidence,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProcedureRecord":
        return cls(
            id=str(data.get("id") or new_id("proc")),
            name=str(data.get("name") or "workflow"),
            workflow_key=str(data.get("workflow_key") or ""),
            trigger_tags=tuple(str(x) for x in data.get("trigger_tags") or ()),
            steps=tuple(str(x) for x in data.get("steps") or ()),
            source_episode_ids=tuple(str(x) for x in data.get("source_episode_ids") or ()),
            success_count=max(0, int(data.get("success_count") or 0)),
            failure_count=max(0, int(data.get("failure_count") or 0)),
            confidence=max(0.0, min(1.0, float(data.get("confidence") or 0.5))),
            status=_procedure_status(str(data.get("status") or "active")),
            created_at=str(data.get("created_at") or _now_iso()),
            updated_at=str(data.get("updated_at") or _now_iso()),
        )

    def with_outcome(self, episode: EpisodeRecord, verdict: str) -> "ProcedureRecord":
        """Apply one observation, recomputing every derived value together.

        Counter, confidence and status move in a single new record, so no
        reader can observe a bumped counter beside a stale confidence.
        `source_episode_ids` doubles as the applied-feedback journal, which is
        what makes re-processing a queue a no-op (see `apply_episode_feedback`).
        """
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
            status="active" if confidence >= 0.6 else "needs_review",
            updated_at=_now_iso(),
        )

    def with_episode(self, episode: EpisodeRecord) -> "ProcedureRecord":
        episode_ids = tuple(dict.fromkeys([*self.source_episode_ids, episode.id]))
        success_count = self.success_count + (1 if episode.outcome == "success" else 0)
        failure_count = self.failure_count + (1 if episode.outcome == "failed" else 0)
        confidence = _smoothed_confidence(success_count, failure_count)
        status: ProcedureStatus = "active" if confidence >= 0.6 else "needs_review"
        return ProcedureRecord(
            id=self.id,
            name=self.name,
            workflow_key=self.workflow_key,
            trigger_tags=self.trigger_tags,
            steps=self.steps,
            source_episode_ids=episode_ids,
            success_count=success_count,
            failure_count=failure_count,
            confidence=confidence,
            status=status,
            created_at=self.created_at,
            updated_at=_now_iso(),
        )


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
            "notes": list(self.notes),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConsolidationReport":
        return cls(
            id=str(data.get("id") or new_id("consol")),
            episode_count=max(0, int(data.get("episode_count") or 0)),
            procedure_count=max(0, int(data.get("procedure_count") or 0)),
            linked_episode_ids=tuple(str(x) for x in data.get("linked_episode_ids") or ()),
            active_procedure_ids=tuple(str(x) for x in data.get("active_procedure_ids") or ()),
            needs_review_procedure_ids=tuple(str(x) for x in data.get("needs_review_procedure_ids") or ()),
            obsolete_procedure_ids=tuple(str(x) for x in data.get("obsolete_procedure_ids") or ()),
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


class EpisodicMemoryStore:
    # Tags that mark episodes as too valuable to evict (e.g. repair lessons).
    PROTECTED_TAGS: frozenset[str] = frozenset({"lesson", "bug-fix", "regression-guard"})

    def __init__(self, path: Path | str, *, max_episodes: int = 200):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_episodes = max_episodes

    def save(self, episode: EpisodeRecord) -> None:
        with state_file_lock(self.path):
            append_state_jsonl_unlocked(self.path, [episode.to_dict()])
            self._maybe_prune_unlocked()

    def save_once(self, episode: EpisodeRecord) -> bool:
        """Append the episode unless its id is already stored.

        Returns True if written, False if it was a duplicate. The lookup and
        the append happen inside ONE `state_file_lock`, so two processes
        racing on the same run cannot both write.

        **Bounded idempotency.** The guarantee lasts only while the episode is
        inside the FIFO window (`max_episodes`): once evicted, its id becomes
        writable again. No separate ledger is kept, so this is deduplication
        for a run and its immediate retries — not a permanent claim.
        """
        with state_file_lock(self.path):
            for row in read_state_jsonl_unlocked(self.path):
                if row.get("id") == episode.id:
                    return False
            append_state_jsonl_unlocked(self.path, [episode.to_dict()])
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
        for ep in episodes:
            haystack = " ".join([ep.goal, ep.question, ep.summary, " ".join(ep.tags)])
            score = len(q_tokens & _tokens(haystack))
            if score:
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
    ) -> tuple["EpisodeRecord | None", float]:
        """Return the episode whose *question* has the highest Jaccard similarity
        to *query* and the similarity score.  Returns ``(None, 0.0)`` when no
        episode reaches *threshold*.

        Only the stored ``question`` field is compared (not goal/summary/tags)
        so the signal is specifically "did the user ask THIS before?" rather than
        "is this topic familiar?".  The Jaccard coefficient is:

            |q_tokens ∩ ep_tokens| / |q_tokens ∪ ep_tokens|

        When a candidate episode has low ``answer_quality_score`` (the previous
        answer was poorly verified), the effective threshold is reduced by 0.10
        so the re-ask hint fires more readily for known-weak episodes.
        """
        q_tokens = _tokens(query)
        if not q_tokens:
            return None, 0.0
        best_ep: "EpisodeRecord | None" = None
        best_score: float = 0.0
        for ep in self.load():
            ep_tokens = _tokens(ep.question)
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
        # Lower the threshold when the best candidate was a low-quality answer
        # — or an unmeasured one. A re-ask hint is an offer to go deeper, not a
        # verdict on the episode, and an answer that carried no evidence is a
        # plausible reason someone is asking again. Treating None as 1.0 made
        # the hint LESS likely exactly where it was most useful (MIR-002).
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

        `allow_credit=False` withholds the POSITIVE direction only, for a cycle
        whose verifier threw: `verified=0, unverified=0` falls through the
        outcome derivation to `success`, so an unmeasured run would otherwise
        credit a procedure exactly like a verified one. The negative direction
        is untouched — a debit comes from a structural failure the verifier had
        no part in, and suppressing it would let a crash shield a procedure
        that genuinely failed. The suppression is reported rather than made to
        look like an absent verdict.

        Driven **only** by `episode.used_procedure_ids`. There is deliberately
        no fallback to workflow_key, tool set, name similarity, a fresh
        retrieval or inference from content: MIR-050 measured that keys pool
        unrelated goals, so any of those would debit a procedure that had
        nothing to do with this run. An id that no longer resolves is reported
        as `orphaned` — never substituted with something that merely looks
        similar.

        `None` (legacy, attribution unknown) and `()` (known to have applied
        none) are both no-ops, and stay distinguishable in the report.

        Idempotent per episode: a procedure whose journal already lists this
        episode id is skipped, so re-processing a queue cannot turn the ratchet
        twice. The check is an explicit journal lookup, not a guess from the
        counters' shape.
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
        """Restore `confidence == _smoothed_confidence(success, failure)` (MIR-051).

        Smoothing landed on 2026-07-11; rows written before it kept a raw
        `confidence` the formula cannot produce — 45 of 65 in the live store
        sat at 1.0, 39 of those on a single success. The writers are correct,
        so this repairs data, not logic, and is deliberately a separate
        explicit pass rather than something hidden inside read or upsert.

        Touches **only** derived values: `confidence`, and `status` with it,
        since status is exactly `confidence >= 0.6`. Leaving status stale would
        just trade one broken invariant for another.

        Never touched: `success_count` / `failure_count` (reconstructing missing
        failure history is MIR-048's problem, not a migration's), `created_at` /
        `updated_at` (refreshing freshness would make stale procedures look
        recently used and outlive hygiene), identity, provenance and content.

        Operates on raw payloads so unknown fields survive untouched and the
        real stored counters are visible — `ProcedureRecord.from_dict` clamps
        negatives to 0, which would silently launder a corrupt row into a
        valid-looking one. Writes through the store's own writer, so the
        integrity envelope is recomputed normally.

        Records already consistent are not rewritten at all, so their bytes,
        checksums and audit trail stay exactly as they were.
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
                row["status"] = "active" if expected >= 0.6 else "needs_review"
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
                updated = proc.with_episode(episode)
                out.append(updated)
                created = False
            else:
                out.append(proc)
        if updated is None:
            updated = candidate.with_episode(episode)
            out.append(updated)
        self.rewrite(out)
        return updated, created

    def search(self, query: str, *, limit: int = 3) -> list[ProcedureRecord]:
        q_tokens = _tokens(query)
        if not q_tokens:
            return []
        scored: list[tuple[int, ProcedureRecord]] = []
        for proc in self.load():
            haystack = " ".join([proc.name, " ".join(proc.trigger_tags), " ".join(proc.steps)])
            score = len(q_tokens & _tokens(haystack))
            if score:
                scored.append((score, proc))
        scored.sort(key=lambda item: (item[0], item[1].confidence, item[1].updated_at), reverse=True)
        return [proc for _score, proc in scored[:limit]]


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


def feedback_for_episode(episode: EpisodeRecord) -> str:
    """What this run's outcome says about a procedure it used.

    Returns "success", "failure" or "none".

    A cancelled run yields "none": cancellation is a control signal, not
    evidence that the procedure is bad. Punishing a procedure because an
    operator pressed stop would make the counters describe scheduling, not
    quality. `partial` DOES count as a failure — unverified support
    outnumbered verified support, which is a real quality signal.
    """
    if any(t.startswith("aborted:cancelled") for t in episode.tags):
        return "none"
    if episode.outcome == "success":
        return "success"
    if episode.outcome in ("partial", "failed"):
        return "failure"
    return "none"


def resolve_used_procedures(
    *, selected: "list[ProcedureRecord]", executed_tools: "list[str]"
) -> tuple[str, ...]:
    """Which of the SELECTED procedures this run actually applied.

    Two gates, both required. A procedure must have been selected into the run
    (retrieval alone is not use), and every tool in its workflow must have
    actually executed — the plan is not evidence, so a run cancelled before
    reaching a procedure's steps does not debit it.

    Attribution follows the selected records, never `workflow_key`: MIR-050
    measured that keys pool unrelated goals, so matching on shape would debit
    whichever procedure happened to share it.

    Order is first actual completion and the result is a tuple, so the stored
    record is deterministic and reproducible — a serialised set would not be.
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


def assemble_completion_state(
    *,
    aborted_reason: str,
    replan_exhausted: bool,
    declared: str | None,
) -> CompletionState:
    """Decide whether the goal was reached. Called ONCE, at banking.

    A closed table, ordered so that any combination of signals resolves the
    same way every time — the priority is the contract, not the order the
    branches happen to be written in:

    ==  ==========================================  =============
    #   predicate                                   state
    ==  ==========================================  =============
    1   aborted_reason == "cancelled"               cancelled
    2   aborted_reason (anything else)              failed
    3   replan_exhausted                            failed
    4   declared is a known token                   that token
    5   otherwise                                   unknown
    ==  ==========================================  =============

    Structural facts outrank the declaration absolutely. An answer claiming it
    achieved the goal is a claim about the answer; a run that was cancelled or
    exhausted its replans is a fact about the run, and the fact wins. The
    declaration is still stored, so a model that says "achieved" over a failed
    run stays auditable.

    The predicates read the CALLER'S arguments, not the tags derived from
    them: tags are this function's downstream, and reading them back would be
    parsing our own output.

    The fast-path replay needs no rule of its own. It carries no abort, no
    exhaustion and no declaration — the synthesizer never ran — so it lands on
    `unknown` through branch 5. Giving it a `source_labels` predicate would
    tie the verdict to a naming convention nothing enforces, to reach a result
    branch 5 already produces.
    """
    if aborted_reason == "cancelled":
        return "cancelled"
    if aborted_reason:
        return "failed"
    if replan_exhausted:
        return "failed"
    if declared in _COMPLETION_DECLARATIONS:
        return declared  # type: ignore[return-value]
    return "unknown"


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


def decide_usage_eligibility(episode: EpisodeRecord) -> bool:
    """Decide whether a freshly banked episode may steer later answers.

    This is the admission policy, kept as a function rather than a literal
    because admission is a judgement about a specific episode, not a global
    switch. It always returns a bool: `None` means "never classified" and is
    reserved for rows written before the field existed — a policy that just
    examined an episode must not manufacture that state.

    Admitted when every one of these holds:

    - ``outcome == "success"`` — the cycle completed and was scored. A
      ``partial`` means unverified support outnumbered verified support,
      which is the self-reinforcement MIR-001 closed; ``failed`` speaks for
      itself.
    - ``verified_chunks > 0`` — something was independently confirmed. This
      is what excludes a pure general-knowledge answer: it may be perfectly
      correct and still carries nothing reusable — no sources, no findings.
    - not a replay — a copy of an earlier answer is not new experience.
      After MIR-041 a replay banks ``verified_chunks=0`` and so is already
      refused by the rule above; the source-label check is defence in depth
      for records written before that fix.

    One deliberate exception: a curated ``lesson`` is admitted whatever its
    outcome. Learning from a failure is the entire purpose of that tag, and
    lessons are already protected from eviction for the same reason.

    No threshold constant appears here on purpose — every rule reads a fact
    the verifier measured, so there is no number to tune or to justify.
    """
    if "lesson" in episode.tags:
        return True
    if episode.outcome != "success":
        return False
    if any(str(label).startswith("memory:") for label in episode.source_labels):
        return False
    return episode.verified_chunks > 0


def is_usage_eligible(episode: EpisodeRecord) -> bool:
    """May this episode steer a later answer?

    Only an EXPLICIT `True` admits it. Both `None` (legacy_unclassified) and
    `False` (quarantined) are refused — fail-closed, because an episode whose
    provenance was never established is not evidence that it is trustworthy.
    """
    return episode.usage_eligible is True


def episode_id_for_run(run_id: str) -> str:
    """Deterministic episode id for one attempt.

    Derived rather than random so `EpisodicMemoryStore.save_once` can detect a
    duplicate of the same run without a side ledger.
    """
    return f"ep-run-{run_id}"


def episode_from_agent_cycle(
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
    outcome: EpisodeOutcome
    if aborted_reason:
        # The run did not finish. No chunk count can express that, so it is
        # decided before them and cannot be overridden into a success.
        outcome = "failed"
    elif replan_exhausted:
        outcome = "failed"
    elif unverified > verified:
        # The answer's UNVERIFIED support outnumbers its verified support — a
        # relative-majority test (mirrors the `weak >= verified` guard below),
        # not a magic threshold. The old `and verified == 0` let a single lucky
        # verified chunk immunise an answer with many more unverified chunks, so
        # verified=1/unverified=10 banked as a clean `success` (CORE-01/MGA-02).
        # (verified=0/unverified=0 is untouched — a pure general-knowledge answer
        # stays `success`; that relevance question is a separate concern.)
        outcome = "partial"
    elif weak > 0 and weak >= verified:
        # The answer leans at least as much on support the verifier could not
        # confirm (sub-agent claims, unmatched citations, missing receipts) as
        # on verified evidence. Not a clean success — do NOT let it graduate to
        # a reusable procedure or bank a 1.0 quality score.
        outcome = "partial"
    else:
        outcome = "success"
    tools = tuple(str(t) for t in tools_used if str(t).strip())
    labels = tuple(str(label) for label in source_labels if str(label).strip())
    tags = _episode_tags(tools=tools, outcome=outcome, labels=labels)
    if aborted_reason:
        tags = tags + ("aborted", f"aborted:{aborted_reason}")
    # A run-derived id is what makes duplicate detection possible at all: the
    # store can recognise "this attempt was already banked" without keeping a
    # ledger. Runs without an id keep the random default.
    episode_id = episode_id_for_run(run_id) if run_id else new_id("ep")
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
        declared_completion=(
            declared_completion
            if declared_completion in _COMPLETION_DECLARATIONS
            else None
        ),
        completion_state=assemble_completion_state(
            aborted_reason=str(aborted_reason or ""),
            replan_exhausted=bool(replan_exhausted),
            declared=declared_completion,
        ),
        id=episode_id,
    )


def procedure_from_episode(episode: EpisodeRecord) -> ProcedureRecord | None:
    if episode.outcome != "success" or not episode.tools_used:
        return None
    workflow_key = "tools:" + "->".join(episode.tools_used)
    readable_tools = ", ".join(episode.tools_used)
    steps = (
        "Understand the user goal and choose the minimal tool sequence.",
        *(f"Run tool: {tool}" for tool in episode.tools_used),
        "Verify tool outputs against the requested answer.",
        "Return cited answer and record the episode outcome.",
    )
    return ProcedureRecord(
        name=f"Workflow using {readable_tools}",
        workflow_key=workflow_key,
        trigger_tags=tuple(dict.fromkeys([*episode.tools_used, *episode.tags])),
        steps=tuple(steps),
        source_episode_ids=(),
        success_count=0,
        failure_count=0,
        confidence=0.5,
        status="active",
    )


def consolidate_memory(
    *,
    episodes: list[EpisodeRecord],
    procedures: list[ProcedureRecord],
) -> ConsolidationReport:
    active = tuple(proc.id for proc in procedures if proc.status == "active")
    needs_review = tuple(proc.id for proc in procedures if proc.status == "needs_review")
    obsolete = tuple(proc.id for proc in procedures if proc.status == "obsolete")
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
    return value if value in {"active", "needs_review", "obsolete"} else "needs_review"  # type: ignore[return-value]


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
