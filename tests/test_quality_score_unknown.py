"""MIR-002 (remainder) — an undefined quality score must not read as a perfect one.

`_compute_quality_score` answers "what fraction of evidence chunks held up?".
With no evidence chunks at all the fraction is UNDEFINED — but it returned
1.0, the top of the scale. Three consumers read that as "perfect", and each
one inverted:

    staleness scoring   a groundless episode outlived a well-evidenced one
    pruning selection   the evidenced episode was discarded, the groundless
                        one shielded
    re-ask detection    the groundless answer was LESS likely to trigger a
                        "you asked again" hint than a weakly-evidenced one

Fixing the number (1.0 -> 0.3) would only move the arbitrary constant around
and still claim a measurement that was never made. The score is therefore
`float | None`, and every consumer states what it does with None:

    staleness      treats it as mid-scale — unknown earns neither the bonus
                   of a perfect score nor the penalty of a bad one
    pruning        does not call it low quality (we do not know that)
    re-ask         lowers the threshold, as for weak answers: an ungrounded
                   answer is a plausible reason to ask again, and the hint is
                   an offer to go deeper, not a punishment
    fast path      refuses to replay it — an answer whose quality was never
                   established must not be served verbatim

Status when written: every test here FAILS.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.episodic_hygiene import score_staleness, select_for_pruning
from core.smart_memory import (
    EpisodeRecord,
    EpisodicMemoryStore,
    _compute_quality_score,
)

_OLD = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()


def _ep(eid: str, verified: int, unverified: int, *, question: str = "q") -> EpisodeRecord:
    return EpisodeRecord(
        goal="g", question=question, outcome="success",  # type: ignore[arg-type]
        summary="s",
        verified_chunks=verified, unverified_chunks=unverified,
        answer_quality_score=_compute_quality_score(verified, unverified, 0),
        id=eid, created_at=_OLD,
    )


# ==========================================================================
# The root: an unmeasurable fraction is not a perfect fraction.
# ==========================================================================
def test_no_evidence_yields_an_undefined_score() -> None:
    assert _compute_quality_score(0, 0, 0) is None, (
        "with no chunks the verified fraction is undefined; returning 1.0 "
        "claims a measurement that was never made"
    )


def test_real_ratios_are_unchanged() -> None:
    """GUARD: the fix must not disturb scores that ARE measurable."""
    assert _compute_quality_score(8, 2, 0) == 0.8
    assert _compute_quality_score(0, 5, 0) == 0.0
    assert _compute_quality_score(5, 0, 1) == round(5 / 6, 3)


# ==========================================================================
# Consumer 1 — staleness scoring
# ==========================================================================
def test_groundless_episode_does_not_outlive_an_evidenced_one() -> None:
    groundless = _ep("groundless", 0, 0)
    evidenced = _ep("evidenced", 8, 2)

    assert score_staleness(groundless) >= score_staleness(evidenced), (
        "a groundless episode scored as fresher than one with 8/10 verified "
        "chunks — higher staleness means pruned sooner, so this inverts which "
        "experience survives"
    )


# ==========================================================================
# Consumer 2 — pruning selection
# ==========================================================================
def test_pruning_does_not_shield_groundless_over_evidenced() -> None:
    groundless = _ep("groundless", 0, 0)
    evidenced = _ep("evidenced", 8, 2)

    selected = {
        e.id
        for e in select_for_pruning(
            [groundless, evidenced],
            min_quality=0.9, max_age_days=30, staleness_threshold=0.0,
        )
    }
    assert selected != {"evidenced"}, (
        "the evidenced episode was selected for pruning while the groundless "
        "one was shielded by its unearned 1.0"
    )


# ==========================================================================
# Consumer 3 — re-ask detection
# ==========================================================================
def test_reask_hint_is_not_less_likely_for_a_groundless_answer(tmp_path) -> None:
    stored = "how do I deploy the service to production"
    asked = "how do I deploy the service"

    def _fires(eid: str, verified: int, unverified: int) -> bool:
        store = EpisodicMemoryStore(tmp_path / f"{eid}.jsonl")
        store.save(_ep(eid, verified, unverified, question=stored))
        hit, _ = store.find_most_similar(asked, threshold=0.85)
        return hit is not None

    groundless_fires = _fires("groundless", 0, 0)
    weak_fires = _fires("weak", 1, 4)

    assert groundless_fires >= weak_fires, (
        "a re-ask after a groundless answer was LESS likely to be noticed than "
        "one after a weakly-evidenced answer"
    )


# ==========================================================================
# Consumer 4 — the fast path must not replay an unmeasured answer
# ==========================================================================
def test_unmeasured_episode_is_not_fast_path_eligible() -> None:
    """The gate compares against 0.70; None must fail it, not crash on it."""
    from core.loop import AgentLoop  # noqa: PLC0415 — import cost only if run

    groundless = _ep("groundless", 0, 0)
    assert groundless.answer_quality_score is None
    assert not AgentLoop._quality_allows_replay(groundless), (
        "an answer whose quality was never established must not be replayed"
    )


def test_measured_high_quality_episode_stays_fast_path_eligible() -> None:
    """GUARD: the legitimate optimisation must survive."""
    from core.loop import AgentLoop  # noqa: PLC0415

    assert AgentLoop._quality_allows_replay(_ep("good", 9, 1))
