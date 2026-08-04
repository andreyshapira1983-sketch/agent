"""Was a self-build veto a verdict on the target, or our own pipeline breaking?

The producer keeps a short cooldown of recently vetoed targets so it advances
instead of hitting the same wall twice. That is only sound when the veto said
something about the target. When the veto happened because the builder's reply
never parsed, the cooldown teaches the agent to avoid exactly the files its own
generator chokes on — the ones it most needs to work on.

Measured 2026-08-04 over the agent's own episodes: four of five `critic_veto`
episodes were pipeline failures, and all five put their target on the avoid list.
"""
from __future__ import annotations

#: Phrases observed in real veto reasons that describe OUR machinery failing,
#: not the target. Taken from `data/episodic_memory.jsonl` (2026-08-04); extend
#: as new failure shapes are actually observed, not as they are imagined.
_PIPELINE_FAILURE_MARKERS: tuple[str, ...] = (
    "did not parse into usable content",
    "empty generated content",
    "no usable content",
    "provider error",
    "rate limit",
    "timed out",
    "timeout",
)


def veto_blames_the_target(reason: str) -> bool:
    """True when the veto is a judgement the target deserves to be avoided for.

    Any marker of our own machinery failing wins: a reply that did not parse
    also drags along "confidence 0.00 below threshold" and "no targeted tests
    specified", both of which describe a patch that was never produced.
    """
    text = (reason or "").casefold()
    if not text.strip():
        return False
    return not any(marker in text for marker in _PIPELINE_FAILURE_MARKERS)
