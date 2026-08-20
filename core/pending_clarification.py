"""The question a clarification asked ABOUT, kept until the operator answers.

checkpoints right semantics — `paused` even carries `original_user_question`
— but `CheckpointLoader.load` needs a `trace_id`, and `cli/resume.py` gets
that from the OPERATOR. A fresh process holding a plain reply has no id.
user_profile right lifetime, wrong subject: who the operator is, not what
they asked. runtime_tasks the autonomous contour's queue, not the
interactive turn. persistent memory knowledge, not a pending task; its
writes are policy-gated and de-duplicated, which is right for memory and
wrong for a task that must survive verbatim exactly once. dialogue history
does not survive the process at all — `dialogue_supported _chunks=0` in the
failing run, history empty.

consumed exactly once `take` reads and deletes in one call. A second caller
gets nothing, so a reply cannot be applied twice. never inherited stale a
record older than `_TTL_SECONDS` is dropped unread. Without it an unrelated
message typed hours later would silently inherit an abandoned task.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

#: See the module docstring: inferred from measured reply latencies, not policy.
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
