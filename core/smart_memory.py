"""Episodic, procedural and consolidation memory for autonomous operation.

This module is intentionally local, deterministic and auditable. It does not
train a model. It stores what happened, turns successful tool workflows into
small reusable procedures, and writes consolidation reports that link episodes
to procedures and surface stale knowledge risks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
ProcedureStatus = Literal["active", "needs_review", "obsolete"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(text: str, *, max_chars: int = 800) -> str:
    redacted, _, _ = redact_dlp_text(str(text or "").strip())
    redacted = " ".join(redacted.split())
    if len(redacted) > max_chars:
        return redacted[: max_chars - 1].rstrip() + "..."
    return redacted


def _compute_quality_score(verified: int, unverified: int, weak: int = 0) -> float:
    """Fraction of evidence chunks that stood up as verified support.

    ``weak`` counts claims the verifier could NOT confirm as faithful support —
    sub-agent-asserted, cited-but-unmatched, receipt-missing and topic-only
    chunks. They belong in the denominator, never in the numerator: an answer
    resting on unconfirmed support must not score as if it were verified.
    Returns 1.0 only when NO evidence chunks of any kind exist (a pure
    general-knowledge answer — neutral, not penalised)."""
    total = verified + unverified + weak
    if total == 0:
        return 1.0
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
    # 1.0 when no evidence chunks were seen (general-knowledge answer — neutral).
    # Computed from the chunk counts; not stored separately in the JSONL.
    answer_quality_score: float = 1.0
    tags: tuple[str, ...] = ()
    # Full answer text — stored verbatim for the episodic fast path.
    # Empty string for episodes created before this field was added.
    full_answer: str = ""
    # Which logical task this episode served, and which attempt produced it.
    # `task_id` survives a retry; `run_id` is fresh per attempt. Empty string
    # on legacy records and on runs that carry no task.
    task_id: str = ""
    run_id: str = ""
    id: str = field(default_factory=lambda: new_id("ep"))
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "run_id": self.run_id,
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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EpisodeRecord":
        return cls(
            id=str(data.get("id") or new_id("ep")),
            task_id=str(data.get("task_id") or ""),
            run_id=str(data.get("run_id") or ""),
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
        q_tokens = _tokens(query)
        if not q_tokens:
            return []
        scored: list[tuple[int, EpisodeRecord]] = []
        for ep in self.load():
            haystack = " ".join([ep.goal, ep.question, ep.summary, " ".join(ep.tags)])
            score = len(q_tokens & _tokens(haystack))
            if score:
                # Boost protected episodes (lessons, bug-fixes) so they surface
                # above ordinary episodes when there is any token overlap.
                if self.PROTECTED_TAGS & set(ep.tags):
                    score += 50
                scored.append((score, ep))
        scored.sort(key=lambda item: (item[0], item[1].created_at), reverse=True)
        return [ep for _score, ep in scored[:limit]]

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
        # Lower the threshold when the best candidate was a low-quality answer.
        effective_threshold = threshold
        if best_ep.answer_quality_score < 0.5:
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
) -> EpisodeRecord:
    verified = max(0, int(verified_chunks))
    unverified = max(0, int(unverified_chunks))
    weak = max(0, int(weak_chunks))
    outcome: EpisodeOutcome
    if replan_exhausted:
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
