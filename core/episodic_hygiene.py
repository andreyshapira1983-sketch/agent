"""Episodic memory hygiene: staleness scoring and pruning of old low-value episodes."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone

from core.smart_memory import EpisodeRecord, EpisodicMemoryStore

_DEFAULT_MAX_AGE_DAYS = 30
_DEFAULT_MIN_QUALITY = 0.4  # below this, the episode is a distractor risk
_DEFAULT_STALENESS_THRESHOLD = 1.5  # tuned with score_staleness's weights


# Score-component weights; module constants so a config can override them.
_W_AGE = 1.0 / 30.0          # +1.0 per 30 days
_W_LOW_QUALITY = 1.5         # full weight when quality == 0
# Unmeasured answers sit mid-scale on the quality axis: neither rewarded nor punished for carrying no evidence.
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
    except (TypeError, ValueError, OverflowError):
        return None
    else:
        return dt


def score_staleness(ep: EpisodeRecord, now: datetime | None = None) -> float:
    """Eviction score (higher = better candidate); protected episodes return 0.0 so they never cross a positive threshold."""
    if EpisodicMemoryStore.PROTECTED_TAGS & set(ep.tags):
        return 0.0
    now = now or datetime.now(timezone.utc)
    created = _parse_iso(ep.created_at)
    age_days = max(0.0, (now - created).total_seconds() / 86400.0) if created else 0.0

    # An unmeasured answer (no evidence chunks) gets mid-scale quality, not 1.0, so groundless episodes cannot outlive well-evidenced ones.
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
    """Return episodes that should be removed.

        Selected iff ALL hold: not protected; older than ``max_age_days``; quality below ``min_quality``
        or outcome failed or ``replan_exhausted``; staleness above ``staleness_threshold``.
        Recent episodes are always kept so the loop can learn from fresh failures.
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
        # Two separate reasons for no reusable value: low_quality (measured, support did not hold up) and
        # unmeasured (no evidence at all). Both are candidates; age, staleness and the protected-tag guard still apply.
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
    keep_ids: frozenset[str] = frozenset(),
) -> list[str]:
    """Apply :func:`select_for_pruning` to ``store`` and delete the selected episodes; returns their IDs (would-be IDs in dry-run)."""
    # Uses the store's public helpers and a re-write, avoiding the file_lock helpers' private API.
    from core.state_integrity import (
        read_state_jsonl_unlocked,
        rewrite_state_jsonl_unlocked,
        state_file_lock,
    )

    with state_file_lock(store.path):
        rows = read_state_jsonl_unlocked(store.path)
        episodes: list[EpisodeRecord] = []
        originals: list[dict] = []  # parallel to `episodes`
        unparsed: list[dict] = []
        for row in rows:
            try:
                episodes.append(EpisodeRecord.from_dict(row))
                originals.append(row)
            except (TypeError, ValueError, OverflowError):
                unparsed.append(row)
        victims = select_for_pruning(
            episodes,
            max_age_days=max_age_days,
            min_quality=min_quality,
            staleness_threshold=staleness_threshold,
            now=now,
        )
        # Credit-anchor episodes (`keep_ids`) are never handed to cleanup.
        if keep_ids:
            victims = [ep for ep in victims if ep.id not in keep_ids]
        victim_ids = {ep.id for ep in victims}
        if dry_run or not victim_ids:
            return [ep.id for ep in victims]
        kept = [(ep, row) for ep, row in zip(episodes, originals, strict=True) if ep.id not in victim_ids]
        kept.sort(key=lambda pair: pair[0].created_at)
        # Kept rows go back as their ORIGINAL dicts (unknown keys intact); a row this reader cannot parse
        # is written back untouched, not treated as stale.
        rewrite_state_jsonl_unlocked(
            store.path, [row for _ep, row in kept] + unparsed,
        )
        return [ep.id for ep in victims]


def stale_candidates(
    episodes: Iterable[EpisodeRecord],
    *,
    now: datetime | None = None,
    limit: int = 10,
) -> list[tuple[EpisodeRecord, float]]:
    """Diagnostic: top-N highest-staleness episodes with their scores, for auditing before deletion."""
    now = now or datetime.now(timezone.utc)
    pairs: list[tuple[EpisodeRecord, float]] = []
    for ep in episodes:
        if EpisodicMemoryStore.PROTECTED_TAGS & set(ep.tags):
            continue
        pairs.append((ep, score_staleness(ep, now)))
    pairs.sort(key=lambda pair: pair[1], reverse=True)
    return pairs[: max(0, limit)]


# Duplicates: `save_once` dedupes by episode id and every tick mints a new one, so a gate that
# blocks identically each tick would bank identical records forever.

def _duplicate_key(ep: EpisodeRecord) -> tuple[str, str, str, str]:
    """What makes two episodes the same lesson rather than two lessons."""
    return (
        (ep.goal or "").strip(),
        (ep.question or "").strip(),
        (ep.summary or "").strip(),
        (ep.outcome or "").strip(),
    )


def _copy_rank(pair: tuple[int, EpisodeRecord]) -> tuple:
    """Which copy of a group survives: eligible, then more verified, then newest."""
    index, ep = pair
    return (ep.usage_eligible is True, ep.verified_chunks or 0, ep.created_at or "", index)


def select_duplicate_episodes(
    episodes: Sequence[EpisodeRecord],
    *,
    keep_ids: frozenset[str] = frozenset(),
) -> list[str]:
    """IDs of every episode except the best copy of each identical group (eligible, then better verified, then newest).

        `keep_ids` and protected episodes are never selected; an empty summary is not a key (no lesson to collapse).
    """
    return [episodes[i].id for i in _victim_indices(episodes, keep_ids)]


def _victim_indices(
    episodes: Sequence[EpisodeRecord], keep_ids: frozenset[str],
) -> list[int]:
    """Positions of the copies to drop, by POSITION not id: legacy rows share a content-hash id."""
    seen: dict[tuple[str, str, str, str], list[tuple[int, EpisodeRecord]]] = {}
    for index, ep in enumerate(episodes):
        if set(ep.tags or ()) & EpisodicMemoryStore.PROTECTED_TAGS:
            continue
        key = _duplicate_key(ep)
        if not key[2]:
            continue
        seen.setdefault(key, []).append((index, ep))
    victims: list[int] = []
    for group in seen.values():
        best = max(group, key=_copy_rank)[0]
        victims.extend(i for i, ep in group if i != best and ep.id not in keep_ids)
    return sorted(victims)


def collapse_duplicate_episodes(
    store: EpisodicMemoryStore,
    *,
    dry_run: bool = False,
    keep_ids: frozenset[str] = frozenset(),
) -> list[str]:
    """Keep one copy of each repeated episode (plus any `keep_ids`). Returns the IDs dropped."""
    from core.state_integrity import (
        read_state_jsonl_unlocked,
        rewrite_state_jsonl_unlocked,
        state_file_lock,
    )

    with state_file_lock(store.path):
        rows = read_state_jsonl_unlocked(store.path)
        parsed: list[tuple[int, EpisodeRecord]] = []
        for row_index, row in enumerate(rows):
            try:
                parsed.append((row_index, EpisodeRecord.from_dict(row)))
            except (TypeError, ValueError, OverflowError):
                continue
        episodes = [ep for _r, ep in parsed]
        picked = _victim_indices(episodes, keep_ids)
        victims = [episodes[i].id for i in picked]
        if picked and not dry_run:
            dropped_rows = {parsed[i][0] for i in picked}
            rewrite_state_jsonl_unlocked(
                store.path,
                [r for n, r in enumerate(rows) if n not in dropped_rows],
            )
    return victims
