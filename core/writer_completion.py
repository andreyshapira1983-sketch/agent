"""The completion verdict a mechanical writer settles from its own outcome."""

from __future__ import annotations

# Coarse episodic outcome -> completion verdict for writers with no other evidence. Deliberately partial: an unknown outcome yields no verdict rather than a guess.
COMPLETION_BY_OUTCOME: dict[str, str] = {
    "success": "achieved",
    "partial": "partially_achieved",
    "failed": "failed",
}


def completion_from_outcome(outcome: str | None) -> str | None:
    """Return the verdict implied by ``outcome``, or ``None`` if unrecognised."""
    return COMPLETION_BY_OUTCOME.get(str(outcome or ""))
