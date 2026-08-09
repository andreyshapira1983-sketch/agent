"""The question a clarification asked ABOUT, kept until the operator answers.

WHY THIS EXISTS RATHER THAN INSTANCE STATE. The first repair held the pending
question on the `AgentLoop`. A live regression on 2026-08-09 disproved that: the
reply arrived with `question_chars=467`, no `clarification_resumed` event, a new
`session_id` and `turn_index=1`. The relevance repair shipped in the SAME
runtime worked in that run, so the code was current — which leaves only one
explanation, and it is the lifecycle: the agent that asks and the agent that
receives the answer are not the same object.

WHY NOT AN EXISTING MECHANISM. Surveyed first, and the survey is the reason this
module is narrow:

  checkpoints         right semantics — `paused` even carries
                      `original_user_question` — but `CheckpointLoader.load`
                      needs a `trace_id`, and `cli/resume.py` gets that from the
                      OPERATOR. A fresh process holding a plain reply has no id.
  user_profile        right lifetime, wrong subject: who the operator is, not
                      what they asked.
  runtime_tasks       the autonomous contour's queue, not the interactive turn.
  persistent memory   knowledge, not a pending task; its writes are policy-gated
                      and de-duplicated, which is right for memory and wrong for
                      a task that must survive verbatim exactly once.
  dialogue history    does not survive the process at all — `dialogue_supported
                      _chunks=0` in the failing run, history empty.

Nothing had BOTH the right ownership and discovery without an id.

TWO PROPERTIES THE STORE OWES, and both are here rather than in the caller:

  consumed exactly once   `take` reads and deletes in one call. A second caller
                          gets nothing, so a reply cannot be applied twice.
  never inherited stale   a record older than `_TTL_SECONDS` is dropped unread.
                          Without it an unrelated message typed hours later
                          would silently inherit an abandoned task.

`_TTL_SECONDS` is INFERRED, not operator-set. Measured from the real exchanges:
the operator answered a clarification in 3 minutes (21:11 -> 21:14) and in 1
minute (20:16 -> 20:17). Thirty minutes is far above a real reply and far below
"a different day". Change it on evidence.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

#: See the module docstring: inferred from measured reply latencies, not policy.
_TTL_SECONDS = 30 * 60


def pending_clarification(path: Path, question: str | None = None) -> str | None:
    """Hold or take the question a clarification was raised about.

    With ``question`` the record is written and ``None`` returned. Without it the
    record is TAKEN — read and removed — so a reply is applied exactly once, and
    ``None`` comes back when there is nothing pending, when it has expired, or
    when the file is unreadable. Every failure degrades to "nothing pending":
    losing the carry-forward costs the operator a repeat, while raising here
    would take down a turn that is otherwise fine.
    """
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
