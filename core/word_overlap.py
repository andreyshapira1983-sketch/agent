"""Rough text similarity: word set and Jaccard overlap."""
from __future__ import annotations

from collections.abc import Set as AbstractSet


def word_set(text: str) -> frozenset[str]:
    """Lower-case words longer than two characters; any non-letter, non-digit separates."""
    return frozenset(
        w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split()
        if len(w) > 2
    )


def jaccard(a: AbstractSet[str], b: AbstractSet[str]) -> float:
    """Share of common words; an empty set on either side gives 0.0."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
