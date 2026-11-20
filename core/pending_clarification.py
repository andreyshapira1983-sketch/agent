"""The question a clarification asked ABOUT, kept until the operator answers.

    Consumed exactly once: `take` reads and deletes in one call. A record older
    than `_TTL_SECONDS` is dropped unread, so an unrelated later message never
    inherits an abandoned task. Own store because checkpoints need a trace_id a
    fresh process lacks, and memory writes are policy-gated and de-duplicated."""
from __future__ import annotations

import json
import time
from pathlib import Path

#: Expiry age, inferred from measured reply latencies, not policy.
_TTL_SECONDS = 30 * 60


def pending_clarification(path: Path, question: str | None = None) -> str | None:
    """Hold or take the question a clarification was raised about."""
    if question is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"question": question, "written_at": time.time()},
                           ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
        return None

    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        path.unlink()                      # taken: exactly one reply may use it
    except (OSError, json.JSONDecodeError):
        try:
            path.unlink()                  # unreadable is still spent
        except OSError:
            pass
        return None

    if time.time() - float(record.get("written_at", 0)) > _TTL_SECONDS:
        return None
    held = record.get("question")
    return held if isinstance(held, str) and held.strip() else None
