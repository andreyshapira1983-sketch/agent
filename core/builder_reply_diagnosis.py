"""Say what was wrong with a builder reply, in words rather than in silence.

The first version of this named only the raw length, so a live rejection that
cost 183 budget units explained nothing — the actual cause was a single stray
backslash. Each answer here is meant to be actionable on its own.

The distinction that matters most is "ran out of room" versus "wrote nonsense":
they used to sound identical, and the veto that followed blamed the target file
(MIR-083), which taught the agent to avoid files its own generator choked on.
"""
from __future__ import annotations

import json
from typing import Any


def why_builder_reply_failed(build: dict[str, Any], *, max_tokens: int) -> str:
    """One sentence naming the real failure behind an unusable builder reply."""
    if build.get("truncated"):
        return (
            f"reply hit the {max_tokens}-token ceiling — "
            "the target is too large for a single pass"
        )
    raw = str(build.get("raw_reply") or "")
    if not raw.strip():
        return "no raw reply was preserved"
    # Judge the JSON part, not the whole raw: the model often wraps the object
    # in prose or a fence, and `json.loads` on the full text would report
    # "Expecting value" instead of the real cause (review round #303).
    start, end = raw.find("{"), raw.rfind("}")
    candidate = raw[start:end + 1] if start != -1 and end > start else raw
    try:
        json.loads(candidate)
    except json.JSONDecodeError as exc:
        near = candidate[max(0, exc.pos - 40): exc.pos + 20].replace("\n", "⏎")
        return f"invalid JSON: {exc.msg} (position {exc.pos}), near: …{near}…"
    except (TypeError, ValueError):  # pragma: no cover — not JSON at all
        return "the reply is not JSON"
    return "JSON parsed, but the field we need is not in it"
