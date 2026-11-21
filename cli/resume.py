"""``--resume <trace_id>``: decide what a previous run leaves to do."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.loop import format_human_response

if TYPE_CHECKING:  # annotations only
    from pathlib import Path


@dataclass(frozen=True)
class ResumeDecision:
    """What ``main()`` does next: return ``exit_code`` if set, else run with ask/file_hint."""

    exit_code: int | None = None
    ask: str | None = None
    file_hint: str | None = None
    # Paused branch only: the sole link from the old paused task to this run (new trace_id).
    resumed_paused_trace: str | None = None


def _resume_question_from_checkpoint(ctx) -> str:
    paused = getattr(ctx, "paused", None) or {}
    if not paused:
        return ctx.question
    original = str(paused.get("original_user_question") or ctx.question)
    planned = paused.get("planned_steps") or []
    completed = paused.get("completed_steps") or []
    remaining = paused.get("remaining_steps") or []
    blocked = paused.get("blocked_model") or {}
    return "\n".join(
        [
            "Resume the interrupted task from this saved budget checkpoint.",
            f"Original user question: {original}",
            f"Active goal: {paused.get('active_goal') or original}",
            f"Interrupted phase: {paused.get('current_phase') or ctx.last_phase}",
            f"Stop reason: {paused.get('stop_reason') or 'budget_exhausted'}",
            f"Blocked model/counter: {json.dumps(blocked, ensure_ascii=False)}",
            f"Completed steps: {json.dumps(completed, ensure_ascii=False)}",
            f"Remaining steps: {json.dumps(remaining, ensure_ascii=False)}",
            f"Planned steps: {json.dumps(planned, ensure_ascii=False)}",
            ("Continue from the remaining steps when they are still relevant. "
            "Do not repeat completed discovery unless it must be refreshed."),
        ]
    )


def resolve_resume(
    trace_id: str,
    *,
    workspace: Path,
    ask: str | None,
    file_hint: str | None,
) -> ResumeDecision:
    """Resolve ``--resume`` into a decision; a paused last phase wins over a cached answer."""
    import re as _re
    # Same allowlist as CheckpointWriter: the id becomes part of a file path (no traversal).
    _SAFE_TRACE_RE = _re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$')
    if not _SAFE_TRACE_RE.match(trace_id):
        print(
            f"[resume] Invalid trace_id {trace_id!r}: "
            "only letters, digits, hyphens and underscores are allowed.",
            file=sys.stderr,
        )
        return ResumeDecision(exit_code=2)
    from core.checkpoint import PHASE_PAUSED as _PHASE_PAUSED
    from core.checkpoint import CheckpointLoader as _CPLoader
    _log_dir = workspace / "logs"
    _loader = _CPLoader(_log_dir)
    _ctx = _loader.load(trace_id)
    if _ctx is None:
        print(
            f"[resume] No usable checkpoint found for trace_id={trace_id!r}. "
            "Running fresh.",
            file=sys.stderr,
        )
    elif _ctx.last_phase == _PHASE_PAUSED and _ctx.paused:
        print(
            f"[resume] Resuming budget-paused checkpoint "
            f"trace_id={trace_id!r} phase={_ctx.paused.get('current_phase')!r}.",
            file=sys.stderr,
        )
        if not ask:
            ask = _resume_question_from_checkpoint(_ctx)
        if not file_hint:
            file_hint = _ctx.file_hint
        return ResumeDecision(
            ask=ask, file_hint=file_hint, resumed_paused_trace=trace_id
        )
    elif _ctx.answer is not None:
        print(
            f"[resume] Replaying cached answer for trace_id={trace_id!r} "
            f"(phase={_ctx.last_phase}, artifacts={list(_ctx.artifacts)})",
            file=sys.stderr,
        )
        if ask or file_hint:
            print(
                "[resume] --ask/--file ignored: this trace already has a saved answer. "
                "Run without --resume to ask a new question.",
                file=sys.stderr,
            )
        print("\n" + format_human_response(_ctx.answer) + "\n")
        return ResumeDecision(exit_code=0)
    else:
        print(
            f"[resume] Checkpoint found but synthesis incomplete "
            f"(last_phase={_ctx.last_phase!r}). Re-running from scratch.",
            file=sys.stderr,
        )
        if not ask:
            ask = _ctx.question
        if not file_hint:
            file_hint = _ctx.file_hint
    return ResumeDecision(ask=ask, file_hint=file_hint)
