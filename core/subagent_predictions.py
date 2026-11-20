"""Helper prediction against fact: reconciliation when a subagent finishes.

A checkable prediction (how many EXTERNAL sources the answer will stand on) is compared with the result's `external_evidence_count`, so systematic
over-promising shows in the journal. The prediction comes from an explicit `expect_sources`, else the first number in the `expect` text, else it is
"unchecked" and counted as such (the unchecked share is a number, not silence).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RELPATH = Path("data") / "subagent_predictions.jsonl"
_FIRST_NUMBER = re.compile(r"(?<![\w.])(\d{1,2})(?![\w.])")


def predicted_sources(expect: str, stated: Any = None) -> tuple[int | None, str]:
    """(number of sources, where it came from: stated | parsed | unchecked)."""
    if isinstance(stated, int) and not isinstance(stated, bool) and 0 <= stated <= 20:
        return stated, "stated"
    m = _FIRST_NUMBER.search(expect or "")
    return (int(m.group(1)), "parsed") if m else (None, "unchecked")


def child_tool_calls(log_dir: Path | str, trace_id: str) -> int | None:
    """How many tool calls the helper made, from its own trace."""
    try:
        text = (Path(log_dir) / f"{trace_id}.jsonl").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return sum(1 for line in text.splitlines() if '"event": "tool_call"' in line)


def compare(expected: int | None, sources: int, status: str) -> bool | None:
    """Whether it came true: the helper finished successfully and returned at least the promised number of sources."""
    if expected is None:
        return None
    return status == "success" and sources >= expected


def record(workspace: Path | str, row: dict[str, Any]) -> None:
    path = Path(workspace) / RELPATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **row}, ensure_ascii=False) + "\n")
    except OSError:
        return


def tally(workspace: Path | str) -> dict[str, Any]:
    """Journal summary: how many predictions are checkable and how many came true."""
    try:
        lines = (Path(workspace) / RELPATH).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    checked = [r for r in rows if r.get("met") is not None]
    missed = [r for r in checked if not r["met"]]
    return {
        "runs": len(rows),
        "checkable": len(checked),
        "met": len(checked) - len(missed),
        "met_share": round((len(checked) - len(missed)) / len(checked), 3) if checked else None,
        "promised_sources": sum(int(r.get("expected_sources") or 0) for r in checked),
        "delivered_sources": sum(int(r.get("sources") or 0) for r in checked),
        "missed_by_role": dict(Counter(str(r.get("role") or "") for r in missed).most_common(5)),
        "usd": round(sum(float(r.get("usd") or 0) for r in rows), 4),
    }


# Plan step intake (moved from core/step_sanitizer.py: these rules concern the helper, not parsing)

def expect_sources_arg(args: dict[str, Any]) -> dict[str, int]:
    """Checkable number of sources from a plan step, or nothing.

        A non-number is not an error: the comparison takes the first number from `expect` or calls the prediction unchecked.
    """
    sources = args.get("expect_sources")
    if isinstance(sources, int) and not isinstance(sources, bool) and 0 <= sources <= 20:
        return {"expect_sources": sources}
    return {}


def subagent_reason(args: dict[str, Any], idx: int, warnings: list[str]) -> tuple[str, str] | None:
    """Why the helper and what it will return, or None (step dropped).

        A helper may be created, but in writing: "why a helper rather than a direct call" and a checkable "what it returns".
    """
    why, expect = args.get("why"), args.get("expect")
    if isinstance(why, str) and why.strip() and isinstance(expect, str) and expect.strip():
        return why.strip()[:400], expect.strip()[:400]
    warnings.append(
        f"step[{idx}]: spawn_subagent requires 'why' (why a sub-agent and not a direct "
        "tool call) and 'expect' (what exactly it will return), dropped"
    )
    return None
