"""Banked: pruning cannot enforce its own cap once protected rows exceed it.

Measured 2026-08-21 while auditing what the agent may learn from.
`EpisodicMemoryStore._maybe_prune_unlocked` computes how many rows to drop from
the TOTAL — `to_remove = len(episodes) - max_episodes` — but may only drop rows
that are not protected:

    kept = evictable[to_remove:] + protected

Once `to_remove` reaches the number of evictable rows the slice is empty and
`kept` is the protected set alone, whose size is whatever it happens to be. The
store then sits above its cap permanently and every further protected write
raises the floor, because nothing can ever evict one.

This is not a hypothetical shape. `PROTECTED_TAGS` contains `lesson`
(core/smart_memory.py:490), and `core/self_build_memory.py:122` writes that tag
on EVERY self-build episode whatever its status or outcome — so the protected
set grows once per self-build pass with no ceiling. The live store today holds
200 episodes at the default cap of 200, of which 127 carry the tag.

The invariant below is the weakest one that separates the two worlds, and it is
about the cap, not about what deserves protection. Which episodes may be kept
forever is a decision recorded in MIR-115, not something a test should settle.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.smart_memory import EpisodeRecord, EpisodicMemoryStore


def _episode(n: int, *, protected: bool) -> EpisodeRecord:
    return EpisodeRecord(
        goal=f"g{n}",
        question=f"q{n}",
        outcome="partial",
        summary=f"s{n}",
        tags=("lesson",) if protected else ("episode",),
        created_at=f"2026-08-{(n % 27) + 1:02d}T00:00:00+00:00",
    )


def test_pruning_still_enforces_the_cap_when_rows_are_evictable(
    workspace: Path,
) -> None:
    """Boundary pin: the mechanism works in the regime it was written for."""
    store = EpisodicMemoryStore(workspace / "episodes.jsonl", max_episodes=5)
    for n in range(9):
        store.save(_episode(n, protected=False))
    assert len(store.load()) <= 5


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, measured 2026-08-21 and banked rather than fixed "
        "(MIR-115): _maybe_prune_unlocked computes to_remove from the total "
        "but may only evict unprotected rows, so once protected rows exceed "
        "max_episodes the store stays above its cap forever and each further "
        "protected write raises the floor. Since every self-build episode is "
        "tagged `lesson` and `lesson` is protected, the protected set grows "
        "without a ceiling. Fix unprescribed: cap the protected set, expire "
        "protection, or stop granting it automatically — three different "
        "policies about what the agent may keep."
    ),
    strict=True,
)
def test_a_store_of_protected_episodes_still_obeys_its_cap(
    workspace: Path,
) -> None:
    store = EpisodicMemoryStore(workspace / "episodes.jsonl", max_episodes=5)
    for n in range(9):
        store.save(_episode(n, protected=True))
    kept = store.load()
    assert len(kept) <= 5, (
        f"the store holds {len(kept)} episodes against a cap of 5 — pruning "
        "cannot evict any of them, so the cap is now decorative"
    )
