"""The completion verdict a mechanical writer settles from its own outcome."""

from __future__ import annotations

# Coarse episodic outcome -> completion verdict, for writers that hold no other
# evidence. Deliberately partial: an outcome outside this table yields no
# verdict rather than a guessed one, so the axis stays unset and visibly so.
COMPLETION_BY_OUTCOME: dict[str, str] = {
    "success": "achieved",
    "partial": "partially_achieved",
    "failed": "failed",
}


def completion_from_outcome(outcome: str | None) -> str | None:
    """Return the verdict implied by ``outcome``, or ``None`` if unrecognised."""
    return COMPLETION_BY_OUTCOME.get(str(outcome or ""))
