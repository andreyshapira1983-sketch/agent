"""The same failure banked over and over is not fourteen lessons.

Background: docs/CODE_NOTES.md, "Episodic duplicates".
"""
from __future__ import annotations

from pathlib import Path

from core.episodic_hygiene import collapse_duplicate_episodes, select_duplicate_episodes
from core.smart_memory import EpisodeRecord, EpisodicMemoryStore


def _ep(summary: str, *, goal: str = "produce self-build patch", outcome: str = "failed"):
    return EpisodeRecord(
        goal=goal, question="self-build-produce", outcome=outcome, summary=summary
    )


def test_a_failure_repeated_verbatim_is_kept_once(tmp_path: Path):
    """Measured 2026-08-14: 14 identical `dirty_tree_wait` episodes, 14 days."""
    store = EpisodicMemoryStore(tmp_path / "episodic.jsonl")
    for _ in range(14):
        store.save(_ep("self-build dirty_tree_wait: git working tree is not clean"))
    store.save(_ep("self-build critic_veto: confidence 0.00 below threshold"))

    victims = select_duplicate_episodes(store.load())

    assert len(victims) == 13, (
        f"{len(victims)} of 15 marked; the identical gate outcome is banked "
        "once per tick and nothing notices it is the same one"
    )
    assert collapse_duplicate_episodes(store) == victims
    kept = store.load()
    assert len(kept) == 2
    assert sum(1 for e in kept if "dirty_tree_wait" in e.summary) == 1
