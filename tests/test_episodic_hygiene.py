"""Tests for core.episodic_hygiene (staleness scoring + pruning)."""

from datetime import datetime, timedelta, timezone

import pytest

from core.episodic_hygiene import (
    score_staleness,
    select_for_pruning,
    stale_candidates,
)
from core.smart_memory import EpisodeRecord


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
