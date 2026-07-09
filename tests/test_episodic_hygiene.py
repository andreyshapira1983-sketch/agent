"""Tests for core.episodic_hygiene (staleness scoring + pruning)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.episodic_hygiene import (
    prune_stale_episodes,
    score_staleness,
    select_for_pruning,
    stale_candidates,
)
from core.smart_memory import EpisodeRecord, EpisodicMemoryStore


def _ep(
    *,
    age_days: float = 0.0,
    outcome: str = "success",
    quality: float = 1.0,
    tags: tuple[str, ...] = (),
    replan_exhausted: bool = False,
    verified: int = 1,
    unverified: int = 0,
    eid: str | None = None,
    now: datetime | None = None,
) -> EpisodeRecord:
    now = now or datetime.now(timezone.utc)
    created = (now - timedelta(days=age_days)).isoformat()
    return EpisodeRecord(
        goal="g", question="q", outcome=outcome, summary="s",  # type: ignore[arg-type]
        verified_chunks=verified, unverified_chunks=unverified,
        replan_exhausted=replan_exhausted,
        answer_quality_score=quality, tags=tags,
        id=eid or f"ep_{age_days}_{outcome}_{quality}",
        created_at=created,
    )


def test_protected_episode_scores_zero():
    ep = _ep(tags=("lesson",), age_days=365, quality=0.0)
    assert score_staleness(ep) == 0.0


def test_score_increases_with_age_and_low_quality():
    now = datetime.now(timezone.utc)
    young_ok = _ep(age_days=1, quality=1.0, now=now)
    old_bad = _ep(age_days=60, quality=0.0, outcome="failed",
                  replan_exhausted=True, now=now)
    assert score_staleness(young_ok, now) < score_staleness(old_bad, now)


def test_select_skips_protected():
    now = datetime.now(timezone.utc)
    eps = [_ep(age_days=90, quality=0.0, outcome="failed", tags=("lesson",), now=now)]
    assert select_for_pruning(eps, now=now) == []


def test_select_skips_recent():
    now = datetime.now(timezone.utc)
    eps = [_ep(age_days=5, quality=0.0, outcome="failed", now=now)]
    assert select_for_pruning(eps, max_age_days=30, now=now) == []


def test_select_keeps_old_high_quality_success():
    now = datetime.now(timezone.utc)
    eps = [_ep(age_days=120, quality=1.0, outcome="success", now=now)]
    assert select_for_pruning(eps, now=now) == []


def test_select_picks_old_low_quality():
    now = datetime.now(timezone.utc)
    eps = [
        _ep(age_days=60, quality=0.1, outcome="partial", now=now, eid="bad"),
        _ep(age_days=60, quality=1.0, outcome="success", now=now, eid="good"),
        _ep(age_days=2, quality=0.0, outcome="failed", now=now, eid="recent_bad"),
    ]
    victims = select_for_pruning(eps, max_age_days=30, now=now)
    ids = {v.id for v in victims}
    assert "bad" in ids
    assert "good" not in ids
    assert "recent_bad" not in ids  # too recent


def test_select_picks_old_failed_even_if_quality_is_high():
    # Edge case: a failed episode with quality=1.0 (no chunks) — still prune.
    now = datetime.now(timezone.utc)
    eps = [_ep(age_days=60, quality=1.0, outcome="failed", now=now, eid="f")]
    victims = select_for_pruning(eps, max_age_days=30, now=now)
    assert any(v.id == "f" for v in victims)


def test_select_invalid_args():
    with pytest.raises(ValueError):
        select_for_pruning([], max_age_days=-1)
    with pytest.raises(ValueError):
        select_for_pruning([], min_quality=2.0)


def test_stale_candidates_returns_top_n_sorted():
    now = datetime.now(timezone.utc)
    eps = [
        _ep(age_days=5, quality=1.0, now=now, eid="a"),
        _ep(age_days=90, quality=0.0, outcome="failed", now=now, eid="b"),
        _ep(age_days=30, quality=0.5, outcome="partial", now=now, eid="c"),
    ]
    pairs = stale_candidates(eps, now=now, limit=2)
    assert len(pairs) == 2
    # Highest score first.
    assert pairs[0][0].id == "b"


def test_prune_stale_episodes_evicts_and_returns_ids(tmp_path: Path):
    store = EpisodicMemoryStore(tmp_path / "ep.jsonl", max_episodes=100)
    now = datetime.now(timezone.utc)
    keep = _ep(age_days=2, quality=1.0, now=now, eid="keep")
    drop = _ep(age_days=60, quality=0.0, outcome="failed",
               replan_exhausted=True, now=now, eid="drop")
    store.save(keep)
    store.save(drop)
    removed = prune_stale_episodes(store, max_age_days=30, now=now)
    assert "drop" in removed
    assert "keep" not in removed
    remaining_ids = {e.id for e in store.load()}
    assert "keep" in remaining_ids
    assert "drop" not in remaining_ids


def test_prune_stale_episodes_dry_run(tmp_path: Path):
    store = EpisodicMemoryStore(tmp_path / "ep.jsonl", max_episodes=100)
    now = datetime.now(timezone.utc)
    drop = _ep(age_days=60, quality=0.0, outcome="failed",
               now=now, eid="drop")
    store.save(drop)
    candidates = prune_stale_episodes(store, max_age_days=30, dry_run=True, now=now)
    assert candidates == ["drop"]
    # Episode still on disk.
    assert {e.id for e in store.load()} == {"drop"}


def test_store_prune_stale_method(tmp_path: Path):
    store = EpisodicMemoryStore(tmp_path / "ep.jsonl")
    now = datetime.now(timezone.utc)
    keep = _ep(age_days=1, quality=1.0, now=now, eid="keep")
    drop = _ep(age_days=120, quality=0.1, outcome="failed", now=now, eid="drop")
    store.save(keep)
    store.save(drop)
    removed = store.prune_stale(max_age_days=30)
    assert removed == ["drop"]
