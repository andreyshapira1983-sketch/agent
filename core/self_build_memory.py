"""Record self-build / self-apply attempt outcomes into episodic memory (best-effort, never raises)."""
from __future__ import annotations

import logging
import re
from typing import Any

# Self-improvement failure signals (collection from episodes and logs, transfer to the problem
# registry, idle action choice) live in core/self_improvement_signals.py.
# Names are re-exported: CLI, campaign, agent_tick and tests take them from here (and patch them
# here).
from core.self_improvement_signals import (  # noqa: F401
    _UNRELIABLE_DETECTOR_SIGNALS,
    _detector_failure_text,
    _recent_self_improvement_events,
    has_fresh_self_improvement_failure,
    idle_self_direction,
    recent_unresolved_self_improvement_failures,
    sync_self_improvement_issue_registry,
)
from core.veto_cause import veto_blames_the_target
from core.writer_completion import COMPLETION_BY_OUTCOME

logger = logging.getLogger(__name__)

# Episode write failures: counted with a reason instead of being swallowed.
STORE_FAILURES: dict[str, Any] = {"count": 0, "last": ""}

#: The four pre-flight gates: they ask «may I start» and run before any
#: pipeline work, so a run they refused produced NO experience. Such an
#: episode is a status line — banked once (searchable under its status tag),
#: never tagged `lesson`, never repeated while an identical row stands.
#: Measured 2026-08-22: 64 wait-rows held half the protected set, 41 of them
#: minted by one unanswered approval. Full account: docs/CODE_NOTES.md,
#: "A blocked gate is not a lesson".
_GATE_WAIT_STATUSES: frozenset[str] = frozenset({
    "budget_kill_switch", "budget_wait", "approval_wait", "dirty_tree_wait",
})

# Empty producer turn: no goal found, nothing to edit. Such records crowded out all real work in
# memory and competence drives showed "no successful task ever".
_NO_WORK_STATUSES: frozenset[str] = frozenset({
    "no_grounded_target", "no_patch", "skipped", "no_llm", "idle", "none",
})
# Gate refusals before work (cooldown, repeat, contradiction, stale request, no verified request,
# approval needed): no experience, no lesson; identical refusals gave identical protected rows.
_GATE_REFUSAL_STATUSES: frozenset[str] = frozenset({
    "denied_cooldown", "denied_repeat", "conflict_block", "stale_proposal",
    "needs_validated_proposal", "approval_required",
})
_DEDUP_STATUSES: frozenset[str] = _GATE_WAIT_STATUSES | _NO_WORK_STATUSES | _GATE_REFUSAL_STATUSES

# Map each command status to a coarse episodic outcome the agent already
# understands (success / partial / failed).
_OUTCOME_BY_STATUS: dict[str, str] = {
    # ── self-build produce ───────────────────────────────────────────────
    "proposed": "success",
    "critic_veto": "failed",
    "value_veto": "failed",
    "no_patch": "partial",
    "budget_wait": "partial",
    "approval_wait": "partial",
    "dirty_tree_wait": "partial",
    "budget_kill_switch": "partial",
    # ── self-apply run ───────────────────────────────────────────────────
    "committed_local": "success",
    "rolled_back": "failed",
    "blocked": "failed",
    "error": "failed",
}

# The completion axis is settled HERE, at banking, as the cycle settles it
# (`core/smart_memory.py:1341`), never recomputed afterwards.
# Leaving it unset was not neutral: `admit_for_storage` grants a `lesson`-tagged record
# `usage_eligible=True`, and retrieval would replay it with no verdict at all.
# The table lives in `core/writer_completion.py` so the backfill of old rows replays this same rule.
_COMPLETION_BY_OUTCOME = COMPLETION_BY_OUTCOME


#: Tags exist so a later attempt can FIND this episode by file, not to hold a
#: manifest of the change. A wide split would otherwise bury the other tags.
_MAX_PATH_TAGS = 10


def _touched_paths(result: dict[str, Any]) -> list[str]:
    """Repo-relative files this attempt changed, normalised for tag lookup."""
    raw = result.get("files_changed") or []
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        path = str(item or "").replace("\\", "/").strip()
        if path and path not in out:
            out.append(path)
    return out[:_MAX_PATH_TAGS]


def build_self_build_episode(
    kind: str, result: dict[str, Any], *, trace_id: str = ""
) -> Any:
    """Build an :class:`EpisodeRecord` describing one attempt (no I/O).

    ``kind`` is ``"self-build-produce"`` or ``"self-apply-run"``; ``result`` is the
    command's own result dict. Returns ``None`` if smart-memory is unavailable.
    """
    try:
        from core.smart_memory import EpisodeRecord
    except Exception:  # noqa: BLE001 — memory journaling is optional
        return None

    status = str(result.get("status") or "unknown")
    outcome = _OUTCOME_BY_STATUS.get(status, "partial")
    target = str(result.get("target_path") or "")
    reason = str(result.get("reason") or "")
    proposal_id = str(result.get("proposal_id") or result.get("approval_id") or "")
    veto = result.get("veto_reasons") or []

    if kind == "self-apply-run":
        goal = f"apply self-build proposal {proposal_id or '?'}".strip()
        details: list[str] = []
        files = result.get("files_changed") or []
        if files:
            details.append(f"files={list(files)}")
        rollback = result.get("rollback_status")
        if rollback and rollback != "none":
            details.append(f"rollback={rollback}")
        summary = f"self-apply {status}: {reason}"
        if details:
            summary += " (" + "; ".join(details) + ")"
    else:  # self-build-produce (and any future producer trigger)
        goal = f"produce self-build patch for {target or 'backlog candidate'}"
        summary = f"self-build {status}: {reason}"
        if veto:
            summary += " | veto: " + "; ".join(str(v) for v in veto)

    # `lesson` confers eligibility, eviction protection and retrieval priority: a pre-flight refusal
    # earns none of that.
    # An empty turn is not a lesson either: the lesson mark is for work done, not for its absence.
    tags = (["self-build", kind, status, outcome]
            if status in _DEDUP_STATUSES
            else ["self-build", "lesson", kind, status, outcome])
    if status == "critic_veto":
        # Which kind of veto this was decides whether the target goes on the
        # cooldown list. Settled here, where the reasons are still in hand.
        blames_target = veto_blames_the_target(
            reason or "; ".join(str(v) for v in veto)
        )
        tags.append("veto_judgement" if blames_target else "veto_pipeline")
    if target:
        tags.append(target)
    # An apply result names `files_changed`, never `target_path`, so the episode carried no path and
    # `recent_self_build_lessons(agent, file)`, which needs the path tag, found nothing.
    for path in _touched_paths(result):
        tags.append(path)

    # Write side of source tracking: record what the run READ in `source_labels`; the producer
    # reports its reads in `sources`, older callers fall back to the target and the files an apply
    # changed.
    sources = [str(s) for s in (result.get("sources") or []) if str(s).strip()]
    if not sources:
        if target:
            sources.append(f"file:{target}")
        sources.extend(f"file:{p}" for p in _touched_paths(result))

    return EpisodeRecord(
        goal=goal[:500],
        question=kind,
        outcome=outcome,  # type: ignore[arg-type]  # one of success/partial/failed
        summary=summary[:2000],
        tags=tuple(dict.fromkeys(t for t in tags if t)),  # dedup, keep order
        source_labels=tuple(dict.fromkeys(sources)),
        # Family link of the tick: a product refusal must be stitchable with the success episode of
        # the same run, otherwise it is structurally unheard.
        trace_id=str(trace_id or ""),
        completion_state=_COMPLETION_BY_OUTCOME.get(  # type: ignore[arg-type]
            outcome, "unknown"
        ),
    )


def record_self_build_episode(
    agent: Any, *, kind: str, result: dict[str, Any], trace_id: str = ""
) -> bool:
    """Persist one attempt outcome to the agent's episodic memory.

    Returns True when an episode was written, False otherwise. Best-effort: any
    failure (no store, bad record, disk error) is swallowed so the caller
    command / tick is never broken by memory journaling.
    """
    try:
        store = getattr(agent, "episodic_store", None)
        if store is None:
            return False
        episode = build_self_build_episode(kind, result, trace_id=trace_id)
        if episode is None:
            return False
        # A producer that hits the same gate twice must not bank a second identical episode.
        # Keyed on CONTENT, not the status label (label-keyed dedup destroys real records). Gate
        # waits only: judging an identical genuine veto is the hygiene collapser's job.
        if str(result.get("status") or "") in _DEDUP_STATUSES:
            key = (episode.goal, episode.question, episode.summary, episode.outcome)
            for old in store.load():
                if (old.goal, old.question, old.summary, old.outcome) == key:
                    return False
        store.save(episode)
    except Exception as exc:  # noqa: BLE001 — journaling must never break the caller
        # The failure is visible: journal and counter, the caller does not fail.
        STORE_FAILURES["count"] += 1
        STORE_FAILURES["last"] = f"{type(exc).__name__}: {exc}"[:300]
        logger.warning("self-build episode not stored (%s): %s", kind, STORE_FAILURES["last"])
        return False
    return True


def _with_untagged_lessons(
    store: Any, target: str, found: list[Any], *, limit: int
) -> list[Any]:
    """Add failed episodes whose summary names the file but whose tags do not."""
    seen = {id(e) for e in found}
    out = list(found)
    for episode in store.search_by_tags(["self-build", "failed"], limit=limit * 8):
        if len(out) >= limit:
            break
        if id(episode) in seen:
            continue
        if _names_path(str(getattr(episode, "summary", "") or ""), target):
            out.append(episode)
    return out


def _names_path(text: str, target: str) -> bool:
    """The text names the whole path: "core/a.py" is not found in "tests/core/a.py.bak"."""
    pattern = r"(?<![\w./\\-])" + re.escape(target) + r"(?![\w/\\-]|\.\w)"
    return re.search(pattern, text) is not None


def recent_self_build_lessons(agent: Any, target: str, *, limit: int = 3) -> list[str]:
    """Short summaries of PAST FAILED self-build attempts for ``target``, newest first; ``[]`` when memory is unavailable. Never raises."""
    try:
        store = getattr(agent, "episodic_store", None)
        if store is None or not target:
            return []
        episodes = store.search_by_tags(
            ["self-build", "failed", target], limit=limit
        )
        if len(episodes) < limit:
            episodes = _with_untagged_lessons(store, target, episodes, limit=limit)
        lessons: list[str] = []
        for episode in episodes:
            summary = str(getattr(episode, "summary", "") or "").strip()
            if summary:
                lessons.append(summary[:300])
    except Exception:  # noqa: BLE001 — lesson recall must never break the caller
        return []
    else:
        return lessons


def _veto_was_about_the_target(episode: Any, tags: tuple[str, ...]) -> bool:
    """Read the classification tag; fall back to the summary for older rows.

    Episodes banked before the classification existed carry neither tag, and
    they are the ones already sitting in the store — judging them by their own
    veto text is the same rule, applied to the only evidence they kept.
    """
    if "veto_pipeline" in tags:
        return False
    if "veto_judgement" in tags:
        return True
    return veto_blames_the_target(str(getattr(episode, "summary", "") or ""))


def recently_vetoed_self_build_targets(agent: Any, *, limit: int = 20) -> frozenset[str]:
    """Target paths whose LAST self-build attempt was critic-vetoed, used by the producer as a short cooldown set.

    Empty set when memory is unavailable; never raises.
    """
    try:
        store = getattr(agent, "episodic_store", None)
        if store is None:
            return frozenset()
        episodes = store.search_by_tags(["self-build", "critic_veto"], limit=limit)
        targets: set[str] = set()
        for episode in episodes:
            tags = tuple(getattr(episode, "tags", ()) or ())
            if not _veto_was_about_the_target(episode, tags):
                continue
            for tag in tags:
                candidate = str(tag).replace("\\", "/").strip()
                if "/" in candidate and (
                    candidate.endswith((".py", ".md"))
                ):
                    targets.add(candidate)
        return frozenset(targets)
    except Exception:  # noqa: BLE001 — cooldown recall must never break the caller
        return frozenset()
