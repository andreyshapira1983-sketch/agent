"""Conversation history compaction (Anthropic 2025 — context engineering).

Anthropic's Sep 2025 guidance on context engineering recommends *compacting*
older conversation turns into a condensed summary rather than dropping them
silently when the context window pressures rise. Our default `WorkingMemory`
trims oldest turns once `max_turns` is exceeded — that addresses MAST FM-1.4
("loss of conversation history") at the storage level but loses the gist.

This module produces a single synthetic `Turn` summarizing N older turns:

* Default summarizer is **deterministic** (no LLM call): it lists each
  user question, the tools that were exercised, and a one-line answer
  excerpt. Cheap, predictable, no extra latency or cost.
* An optional ``summarizer`` callable can be supplied for richer LLM-
  driven compaction (e.g. via Haiku). The callable receives the list of
  `Turn` objects and must return the summary string.

Usage:

    new_turns, summary_turn = compact_turns(mem.turns, keep_recent=3)
    if summary_turn is not None:
        mem.turns = new_turns

Always returns a list whose first element (when compaction occurred) is
the synthetic summary turn carrying the index of the latest dropped turn,
followed by the kept recent turns in original order.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Sequence

from core.ids import new_id
from core.memory import Turn


_DEFAULT_KEEP_RECENT = 3
_SUMMARY_QUESTION_PREFIX = "[compacted history]"


Summarizer = Callable[[Sequence[Turn]], str]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def default_summarizer(turns: Sequence[Turn]) -> str:
    """Deterministic, LLM-free summary of older turns."""
    if not turns:
        return ""
    lines: list[str] = []
    for t in turns:
        q = t.question.strip().replace("\n", " ")
        if len(q) > 140:
            q = q[:139].rstrip() + "…"
        ans = t.answer.strip().replace("\n", " ")
        if len(ans) > 200:
            ans = ans[:199].rstrip() + "…"
        tools = ", ".join(t.tools_used) if t.tools_used else "(none)"
        lines.append(
            f"- turn {t.index}: q={q!r} | tools={tools} | answer={ans!r}"
        )
    header = (
        f"Summary of {len(turns)} earlier turn(s) "
        f"(turns {turns[0].index}…{turns[-1].index}):"
    )
    return header + "\n" + "\n".join(lines)


def compact_turns(
    turns: Sequence[Turn],
    keep_recent: int = _DEFAULT_KEEP_RECENT,
    summarizer: Summarizer | None = None,
) -> tuple[list[Turn], Turn | None]:
    """Fold turns older than the last ``keep_recent`` into one summary Turn.

    Returns ``(new_turns, summary_turn)``. If no compaction happened
    (not enough turns, or ``keep_recent`` >= len), ``summary_turn`` is
    ``None`` and ``new_turns`` mirrors the original list.

    The synthetic summary turn:
    * `index` — index of the LAST dropped turn (so the kept turns'
      indices remain strictly greater).
    * `id` — fresh `turn_…` id.
    * `question` — `[compacted history]`.
    * `tools_used` — concatenation of tools across dropped turns
      (deduplicated, preserved order).
    * `artifact_labels` — same idea for labels.
    * `answer` — output of the summarizer.
    * `planner_reasoning` — empty.
    """
    if keep_recent < 0:
        raise ValueError("keep_recent must be >= 0")
    n = len(turns)
    if n <= keep_recent:
        return list(turns), None
    older = list(turns[: n - keep_recent])
    recent = list(turns[n - keep_recent :])
    if not older:
        return recent, None

    fn = summarizer or default_summarizer
    summary_text = fn(older)

    seen_tools: set[str] = set()
    tools_combined: list[str] = []
    for t in older:
        for tool in t.tools_used:
            if tool not in seen_tools:
                seen_tools.add(tool)
                tools_combined.append(tool)

    seen_labels: set[str] = set()
    labels_combined: list[str] = []
    for t in older:
        for lbl in t.artifact_labels:
            if lbl not in seen_labels:
                seen_labels.add(lbl)
                labels_combined.append(lbl)

    summary_turn = Turn(
        index=older[-1].index,
        id=new_id("turn"),
        timestamp=_now(),
        question=_SUMMARY_QUESTION_PREFIX,
        planner_reasoning="",
        tools_used=tools_combined,
        artifact_labels=labels_combined,
        answer=summary_text,
    )
    return [summary_turn, *recent], summary_turn
