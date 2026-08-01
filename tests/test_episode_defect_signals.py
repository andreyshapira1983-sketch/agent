"""The episode must record what went wrong in the run, not only what was answered.

Measured before this existed: the agent lost the output of its own shell command
twice in a row, and both runs banked as clean successes. Every sensor logged its
verdict and dropped it, so nothing in the agent's own memory said a fault had
ever occurred — and a fault nobody records is a fault available to repeat.

These tests pin the recording only. `defect_signals` deliberately decides
nothing: promoting a sensor from observer to decider is the operator's call
(`docs/audit/SENSOR_SIGNAL_MEASUREMENT.md`, S3/S4). The last group here guards
that boundary — if banking a signal ever starts changing a verdict, it fails.
"""
from __future__ import annotations

from core.smart_memory import (
    EpisodeRecord,
    episode_from_agent_cycle,
    procedure_credit_allowed,
    procedure_from_episode,
)


def _episode(**kw):
    base = dict(
        goal="g",
        question="q",
        answer="a",
        tools_used=["file_read"],
        source_labels=["file:x"],
        verified_chunks=3,
        unverified_chunks=0,
    )
    base.update(kw)
    return episode_from_agent_cycle(**base)


# ---------------------------------------------------------------------------
# Three states: never collected / collected nothing / collected something
# ---------------------------------------------------------------------------

def test_signals_default_to_none_meaning_never_collected():
    """A caller that cannot collect signals must not claim none fired."""
    assert _episode().defect_signals is None


def test_empty_tuple_means_looked_and_saw_nothing():
    assert _episode(defect_signals=[]).defect_signals == ()


def test_fired_signals_are_recorded():
    episode = _episode(defect_signals=["reasoning_action_mismatch"])
    assert episode.defect_signals == ("reasoning_action_mismatch",)


def test_none_and_empty_are_distinguishable():
    """The difference between 'we never looked' and 'we saw nothing' is the point."""
    assert _episode().defect_signals is None
    assert _episode(defect_signals=()).defect_signals == ()


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def test_repeated_signal_is_recorded_once():
    """A sensor firing on three attempts is one fault, not three."""
    episode = _episode(defect_signals=["x", "x", "x"])
    assert episode.defect_signals == ("x",)


def test_order_is_preserved():
    episode = _episode(defect_signals=["b", "a", "c"])
    assert episode.defect_signals == ("b", "a", "c")


def test_blank_signals_are_dropped():
    episode = _episode(defect_signals=["", "  ".strip(), "real"])
    assert episode.defect_signals == ("real",)


# ---------------------------------------------------------------------------
# Round trip — the record must survive being written and read back
# ---------------------------------------------------------------------------

def test_round_trip_preserves_signals():
    episode = _episode(defect_signals=["reasoning_action_mismatch"])
    back = EpisodeRecord.from_dict(episode.to_dict())
    assert back.defect_signals == ("reasoning_action_mismatch",)


def test_round_trip_preserves_the_empty_tuple():
    back = EpisodeRecord.from_dict(_episode(defect_signals=[]).to_dict())
    assert back.defect_signals == ()


def test_legacy_row_without_the_key_reads_as_none():
    """A row written before this axis existed must stay recognisably legacy."""
    row = _episode().to_dict()
    assert "defect_signals" not in row, (
        "an absent key is the honest encoding of a legacy row; writing null "
        "would make every old episode look like one we examined"
    )
    assert EpisodeRecord.from_dict(row).defect_signals is None


def test_a_row_with_signals_carries_the_key():
    row = _episode(defect_signals=["x"]).to_dict()
    assert row["defect_signals"] == ["x"]


# ---------------------------------------------------------------------------
# The boundary: recording must not become deciding
# ---------------------------------------------------------------------------

def test_signals_do_not_change_the_outcome():
    clean = _episode(defect_signals=[])
    faulty = _episode(defect_signals=["reasoning_action_mismatch"])
    assert faulty.outcome == clean.outcome


def test_signals_do_not_change_the_completion_state():
    clean = _episode(defect_signals=[], declared_completion="achieved")
    faulty = _episode(
        defect_signals=["obligation_silently_missing"],
        declared_completion="achieved",
    )
    assert faulty.completion_state == clean.completion_state


def test_signals_do_not_change_procedure_credit():
    """Denying credit on a signal would be a promotion the operator has not made."""
    clean = _episode(defect_signals=[], declared_completion="achieved")
    faulty = _episode(
        defect_signals=["obligation_silently_missing"],
        declared_completion="achieved",
    )
    assert procedure_credit_allowed(faulty) == procedure_credit_allowed(clean)
    assert (procedure_from_episode(faulty) is None) == (
        procedure_from_episode(clean) is None
    )
