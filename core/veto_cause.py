"""Tell whether a self-build veto judged the target or our own pipeline failing."""
from __future__ import annotations

# Phrases from real veto reasons that describe our machinery failing, not the target.
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
    """True when the veto is a judgement the target deserves to be avoided for."""
    text = (reason or "").casefold()
    if not text.strip():
        return False
    return not any(marker in text for marker in _PIPELINE_FAILURE_MARKERS)
