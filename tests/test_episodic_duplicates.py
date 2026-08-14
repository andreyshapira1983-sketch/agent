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


def test_the_survivor_is_the_newest(tmp_path: Path):
    """The last occurrence carries the current state; older copies add nothing."""
    store = EpisodicMemoryStore(tmp_path / "episodic.jsonl")
    for _ in range(3):
        store.save(_ep("same failure"))
    newest = store.load()[-1].id

    collapse_duplicate_episodes(store)

    assert [e.id for e in store.load()] == [newest]


def test_different_failures_are_not_collapsed(tmp_path: Path):
    """A summary is the lesson; two different ones are two lessons."""
    store = EpisodicMemoryStore(tmp_path / "episodic.jsonl")
    store.save(_ep("dirty_tree_wait"))
    store.save(_ep("no_grounded_target"))
    store.save(_ep("dirty_tree_wait", goal="another goal"))

    assert select_duplicate_episodes(store.load()) == []


def test_a_dry_run_removes_nothing(tmp_path: Path):
    """Sensor first: the caller decides whether the store is rewritten."""
    store = EpisodicMemoryStore(tmp_path / "episodic.jsonl")
    for _ in range(4):
        store.save(_ep("same failure"))

    assert len(collapse_duplicate_episodes(store, dry_run=True)) == 3
    assert len(store.load()) == 4
