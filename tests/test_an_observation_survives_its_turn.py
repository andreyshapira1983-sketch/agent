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


def test_a_different_defect_is_a_different_record(tmp_path: Path):
    """Two defects are two investigations; merging them would lose both."""
    store = CausalObservationStore(tmp_path / "causal.jsonl")
    store.record(_obs(signal="citation_fabricated"))
    store.record(_obs(signal="reasoning_action_mismatch"))

    assert len(store.load()) == 2
