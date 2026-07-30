"""The completion verdict a mechanical writer settles from its own outcome.

`outcome` and `completion_state` are kept deliberately apart
(`core/smart_memory.py:28-32`, MIR-057): the first answers "were the claims
supported", the second "was the goal reached". A cycle can be impeccably
supported and still have answered nothing, so the cycle never infers one from
the other — it assembles the verdict from the synthesizer's declaration and the
abort reason via :func:`core.smart_memory.assemble_completion_state`.

That separation holds for the *cycle*. It does not describe every writer.

Some writers bank an episode about a mechanical attempt — produce a patch, apply
it, repair a file — where there is no synthesizer, no declaration and no abort
reason, and where the attempt's own result *is* the goal. For those, "the patch
was proposed" and "the goal was reached" are not two facts that might disagree;
they are one fact recorded once. This module is the single place that says so,
and its licence extends no further than writers of that shape.

**Where the table may be applied.** Nowhere directly except a writer settling
the axis for an episode it is banking right now. Applying it to stored records
in bulk would re-open the very inference MIR-057 forbids, because the store also
holds cycle-banked rows whose ``outcome`` says nothing about their goal. Rows
banked before their writer learned to settle the axis are reached through
:mod:`scripts.completion_backfill`, which must prove a writer signature *first*
and only then consult this table.

The dependency runs one way: this module knows nothing about any caller.
"""

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
