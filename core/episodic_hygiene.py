"""Episodic memory hygiene — staleness scoring and pruning.

Chroma's *Context Rot* study (Jul 2025) shows model performance degrades
non-uniformly with input length on simple tasks; distractors compound
the drop. Our :class:`core.smart_memory.EpisodicMemoryStore` already
caps the file at ``max_episodes``, but the eviction is purely FIFO over
*non-protected* episodes.  That's coarse:

* It evicts useful old high-quality answers before junk recent ones.
* It keeps low-quality ``replan_exhausted`` failures around as long as
  they are recent — these end up as distractors in retrieval.

This module computes a **staleness score** combining age, outcome,
quality, and explicit failure flags so the store can prune the worst
candidates first. Protected tags (``lesson``, ``bug-fix``,
``regression-guard``) are still untouchable.

Public API:

* :func:`score_staleness(ep, now)` — pure scoring function. Higher = more
  worth evicting. Always non-negative; ``inf`` for protected = NEVER.
* :func:`select_for_pruning(episodes, *, max_age_days, ...)` — returns
  the subset whose score crosses the threshold and that are old enough.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from core.smart_memory import EpisodeRecord, EpisodicMemoryStore


_DEFAULT_MAX_AGE_DAYS = 30
_DEFAULT_MIN_QUALITY = 0.4  # below this, the episode is a distractor risk
_DEFAULT_STALENESS_THRESHOLD = 1.5  # tuned with score_staleness's weights


# Score-component weights. Exposed as module constants so a future config
# can override without code change.
_W_AGE = 1.0 / 30.0          # +1.0 per 30 days
_W_LOW_QUALITY = 1.5         # full weight when quality == 0
# Where an unmeasured answer sits on the quality axis. Mid-scale is a
# statement about this consumer's treatment of missing data, not a guess at
# the episode's worth: it neither rewards nor punishes an answer that carried
# no evidence to measure.
_QUALITY_WHEN_UNMEASURED = 0.5
_W_FAILED = 0.75
_W_PARTIAL = 0.25
_W_REPLAN_EXHAUSTED = 0.50


def _parse_iso(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        # Python 3.11+ tolerates the trailing Z; still play safe.
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def score_staleness(ep: EpisodeRecord, now: datetime | None = None) -> float:
    """Higher value = better candidate for eviction.

    Protected episodes return ``float("inf")`` from the *protection*
    side: callers are expected to filter these out *before* calling
    :func:`select_for_pruning`. To keep this function safe to use in
    isolation it returns ``0.0`` for protected episodes — that way they
    never cross any positive threshold and won't be selected.
    """
    if EpisodicMemoryStore.PROTECTED_TAGS & set(ep.tags):
        return 0.0
    now = now or datetime.now(timezone.utc)
    created = _parse_iso(ep.created_at)
    age_days = max(0.0, (now - created).total_seconds() / 86400.0) if created else 0.0

    # An unmeasured answer (no evidence chunks at all) earns neither the bonus
    # of a perfect score nor the penalty of a bad one — mid-scale is the honest
    # placement for "we do not know". Reading None as 1.0 is what let groundless
    # episodes outlive well-evidenced ones (MIR-002).
    raw_quality = ep.answer_quality_score
    quality = (
        _QUALITY_WHEN_UNMEASURED
        if raw_quality is None
        else max(0.0, min(1.0, float(raw_quality)))
    )
    score = age_days * _W_AGE
    score += (1.0 - quality) * _W_LOW_QUALITY
    if ep.outcome == "failed":
        score += _W_FAILED
    elif ep.outcome == "partial":
        score += _W_PARTIAL
    if ep.replan_exhausted:
        score += _W_REPLAN_EXHAUSTED
    return score


def select_for_pruning(
    episodes: Sequence[EpisodeRecord],
    *,
    max_age_days: int = _DEFAULT_MAX_AGE_DAYS,
    min_quality: float = _DEFAULT_MIN_QUALITY,
    staleness_threshold: float = _DEFAULT_STALENESS_THRESHOLD,
    now: datetime | None = None,
) -> list[EpisodeRecord]:
    """Return episodes that *should* be removed.

    An episode is selected iff ALL of:

    * it is NOT protected (no PROTECTED_TAGS),
    * it is older than ``max_age_days``,
    * its ``answer_quality_score`` is below ``min_quality``
      OR its outcome is ``failed`` OR ``replan_exhausted`` is True,
    * its computed staleness score exceeds ``staleness_threshold``.

    Selection is intentionally conservative: an old high-quality success
    is kept even past the age bar.  Recent (≤ ``max_age_days``) episodes
    are *always* kept regardless of quality so the loop can still learn
    from very recent failures.
    """
    if max_age_days < 0:
        raise ValueError("max_age_days must be >= 0")
    if not 0.0 <= min_quality <= 1.0:
        raise ValueError("min_quality must be in [0, 1]")
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    out: list[EpisodeRecord] = []
    for ep in episodes:
        if EpisodicMemoryStore.PROTECTED_TAGS & set(ep.tags):
            continue
        created = _parse_iso(ep.created_at)
        if created is None or created > cutoff:
            continue
        # Two distinct reasons an episode carries no reusable value, kept
        # apart because they are different facts:
        #   low_quality — measured, and most of its support did not hold up
        #   unmeasured  — carried no evidence at all, so there is nothing to
        #                 reuse: no sources, no verified findings
        # Both are candidates. Treating `unmeasured` as pristine is what let a
        # groundless episode outlive one with 8/10 chunks verified (MIR-002).
        # Neither is deleted on this basis alone — age, staleness and the
        # protected-tag guard still apply.
        low_quality = (
            ep.answer_quality_score is not None
            and ep.answer_quality_score < min_quality
        )
        unmeasured = ep.answer_quality_score is None
        is_failure = ep.outcome == "failed" or ep.replan_exhausted
        if not (low_quality or unmeasured or is_failure):
            continue
        if score_staleness(ep, now) < staleness_threshold:
            continue
        out.append(ep)
    return out


def prune_stale_episodes(
    store: EpisodicMemoryStore,
    *,
    max_age_days: int = _DEFAULT_MAX_AGE_DAYS,
    min_quality: float = _DEFAULT_MIN_QUALITY,
    staleness_threshold: float = _DEFAULT_STALENESS_THRESHOLD,
    dry_run: bool = False,
    now: datetime | None = None,
) -> list[str]:
    """Apply :func:`select_for_pruning` to ``store`` and remove the
    selected episodes. Returns the IDs of episodes that were (or, in
    dry-run mode, would have been) deleted.

    The store's file lock is held for the read+rewrite to avoid races
    with concurrent ``save`` calls.
    """
    # We use the store's public helpers + a re-write so we do not depend
    # on the file_lock helpers' private API. EpisodicMemoryStore.save
    # already serializes appends, and we re-serialize the kept rows.
    from core.state_integrity import (
        read_state_jsonl_unlocked,
        rewrite_state_jsonl_unlocked,
        state_file_lock,
    )

    with state_file_lock(store.path):
        rows = read_state_jsonl_unlocked(store.path)
        episodes: list[EpisodeRecord] = []
        for row in rows:
            try:
                episodes.append(EpisodeRecord.from_dict(row))
            except (TypeError, ValueError):
                continue
        victims = select_for_pruning(
            episodes,
            max_age_days=max_age_days,
            min_quality=min_quality,
            staleness_threshold=staleness_threshold,
            now=now,
        )
        victim_ids = {ep.id for ep in victims}
        if dry_run or not victim_ids:
            return [ep.id for ep in victims]
        kept = [ep for ep in episodes if ep.id not in victim_ids]
        kept.sort(key=lambda e: e.created_at)
        rewrite_state_jsonl_unlocked(store.path, [e.to_dict() for e in kept])
        return [ep.id for ep in victims]


def stale_candidates(
    episodes: Iterable[EpisodeRecord],
    *,
    now: datetime | None = None,
    limit: int = 10,
) -> list[tuple[EpisodeRecord, float]]:
    """Diagnostic: top-N highest-staleness episodes with their scores.

    Useful for an `:audit memory` command before committing to deletion.
    """
    now = now or datetime.now(timezone.utc)
    pairs: list[tuple[EpisodeRecord, float]] = []
    for ep in episodes:
        if EpisodicMemoryStore.PROTECTED_TAGS & set(ep.tags):
            continue
        pairs.append((ep, score_staleness(ep, now)))
    pairs.sort(key=lambda pair: pair[1], reverse=True)
    return pairs[: max(0, limit)]
