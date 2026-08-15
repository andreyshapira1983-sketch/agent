"""The first rung: an observation that dies in the journal cannot be climbed.

Background: docs/CODE_NOTES.md, "The first rung is surviving the turn".
"""
from __future__ import annotations

from pathlib import Path

from core.causal_lesson import Observation
from core.causal_store import CausalObservationStore


def _obs(signal: str = "citation_fabricated", episode: str = "ep_1") -> Observation:
    return Observation(
        episode_id=episode, trace_id="tr_1", run_id="run_1",
        defect_signals=(signal,),
        evidence_refs=("file:core/loop.py",),
        observed_mismatch=f"детекторы {signal} при завершении achieved",
    )


def test_an_observation_is_still_there_next_turn(tmp_path: Path):
    """Measured 2026-08-15: `_record_causal_observation` logs and stores
    nothing — its own docstring says «В хранилище ничего не кладётся». So every
    observation the detectors produced died where it was written, and the
    ladder above it had no bottom step to stand on (MIR-096).
    """
    store = CausalObservationStore(tmp_path / "causal.jsonl")
    store.record(_obs())

    again = CausalObservationStore(tmp_path / "causal.jsonl")
    assert len(again.load()) == 1
    assert again.load()[0].defect_signals == ("citation_fabricated",)


def test_the_same_defect_twice_is_one_record_that_counts(tmp_path: Path):
    """Repetition is the signal that a defect is a class, not an accident.

    Fourteen identical `dirty_tree_wait` episodes taught nothing because each
    was a fresh row (MIR-090). Here the second sighting raises `occurrences`
    and moves `last_seen`, so «случалось дважды» becomes readable instead of
    being reconstructable only by counting rows.
    """
    store = CausalObservationStore(tmp_path / "causal.jsonl")
    store.record(_obs(episode="ep_1"))
    store.record(_obs(episode="ep_2"))

    records = store.load()
    assert len(records) == 1
    assert records[0].occurrences == 2
    assert records[0].episode_ids == ("ep_1", "ep_2")


def test_a_different_defect_is_a_different_record(tmp_path: Path):
    """Two defects are two investigations; merging them would lose both."""
    store = CausalObservationStore(tmp_path / "causal.jsonl")
    store.record(_obs(signal="citation_fabricated"))
    store.record(_obs(signal="reasoning_action_mismatch"))

    assert len(store.load()) == 2


def test_it_stores_observations_only(tmp_path: Path):
    """The store holds the bottom rung and nothing above it.

    A record here is «стоит расследовать», never «доказано»: the tag `lesson`
    is earned through `state_of`, and no write to this file may shortcut it.
    """
    store = CausalObservationStore(tmp_path / "causal.jsonl")
    store.record(_obs())

    raw = (tmp_path / "causal.jsonl").read_text(encoding="utf-8")
    assert "lesson" not in raw
    assert "GENERALIZED" not in raw
