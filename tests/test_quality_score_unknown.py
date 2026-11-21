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
# Consumer 3 — re-ask detection
# ==========================================================================
def test_reask_hint_is_not_less_likely_for_a_groundless_answer(tmp_path) -> None:
    """At a bar only the lowered threshold clears, groundless fires like weak; good does not.

    Similarity here is 2/3: below the 0.7 bar, above the lowered 0.6 one.
    """
    stored = "how do I deploy the service to production"
    asked = "how do I deploy the service"

    def _fires(eid: str, verified: int, unverified: int) -> bool:
        store = EpisodicMemoryStore(tmp_path / f"{eid}.jsonl")
        store.save(_ep(eid, verified, unverified, question=stored))
        hit, _ = store.find_most_similar(asked, threshold=0.7)
        return hit is not None

    assert not _fires("good", 9, 1), (
        "control: a well-evidenced answer must not clear the bar, or the "
        "checks below prove nothing about the lowered threshold"
    )
    assert _fires("weak", 1, 4), "a weakly-evidenced answer no longer lowers the bar"
    assert _fires("groundless", 0, 0), (
        "a re-ask after a groundless answer was LESS likely to be noticed than "
        "one after a weakly-evidenced answer"
    )


# ==========================================================================
# Consumer 4 — the fast path must not replay an unmeasured answer
# ==========================================================================
def test_unmeasured_episode_is_not_fast_path_eligible() -> None:
    """The gate compares against 0.70; None must fail it, not crash on it."""
    from core.loop import AgentLoop

    groundless = _ep("groundless", 0, 0)
    assert groundless.answer_quality_score is None
    assert not AgentLoop._quality_allows_replay(groundless), (
        "an answer whose quality was never established must not be replayed"
    )


def test_measured_high_quality_episode_stays_fast_path_eligible() -> None:
    """GUARD: the legitimate optimisation must survive."""
    from core.loop import AgentLoop

    assert AgentLoop._quality_allows_replay(_ep("good", 9, 1))


def test_the_gate_itself_refuses_an_unmeasured_answer() -> None:
    """The docstring's contract is about the GATE, not one term inside it.

    The two tests above ask `_quality_allows_replay` directly, so deleting its
    call from `_fast_path_allows_replay` left all 7139 tests green (measured
    2026-08-07). An episode is built that clears every OTHER condition, so the
    quality term is the only thing left to refuse it — and the measured twin
    is asserted to pass, which is what proves the refusal is not coming from
    somewhere else.
    """
    from dataclasses import replace

    from core.loop import AgentLoop

    def _replayable(episode):
        return replace(episode, full_answer="stored answer", completion_state="achieved")

    measured = _replayable(_ep("measured", 9, 1))
    groundless = _replayable(_ep("groundless", 0, 0))

    assert AgentLoop._fast_path_allows_replay(measured, 1.0), (
        "control: this episode must clear the gate, or the assertion below "
        "proves nothing about the quality term"
    )
    assert groundless.answer_quality_score is None
    assert not AgentLoop._fast_path_allows_replay(groundless, 1.0), (
        "an answer whose quality was never established must not be replayed"
    )
