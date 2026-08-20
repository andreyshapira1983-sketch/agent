#!/usr/bin/env python3
"""Read-only view of this agent's own state, for whoever is watching it.

## Read-only, and that is a design decision rather than a first version

No `mcp` import here on purpose. That package brings 18 direct dependencies
— starlette, uvicorn, sse-starlette, websockets, pyjwt[crypto], typer, rich
— and this repository pins and locks everything it ships, with an SBOM. A
read-only viewer the agent never imports does not justify that expansion of
the supply chain, so the transport lives in `agent_mcp_server.py` and the
reading lives here, testable with nothing installed.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

_DATA = REPO / "data"
_LOGS = REPO / "logs"


def _read_jsonl(path: Path, *, last: int = 0) -> Any:
    """Rows of a JSONL store, or a named error -- never a silent empty list."""
    if not path.exists():
        return {"error": "missing", "store": path.name,
                "detail": "the store has never been written"}
    kept: Any = collections.deque(maxlen=last) if last else []
    total = 0
    unreadable = 0
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    # Counted, not hidden: a row that will not parse is a row
                    # the agent itself cannot read either, and the count is the
                    # only way a reader learns it existed.
                    unreadable += 1
                    continue
                total += 1
                kept.append(row)
    except OSError as exc:
        return {"error": type(exc).__name__, "store": path.name,
                "detail": str(exc)[:200]}
    return {"rows": list(kept), "total": total, "unreadable_rows": unreadable}


def _payload(row: dict) -> dict:
    """Records are written as `{_integrity, payload}` — unwrap when wrapped."""
    inner = row.get("payload")
    return inner if isinstance(inner, dict) else row


def agent_status() -> str:
    """Daemon liveness, run mode, pending approvals, self-build state.

    And notebook section 7: diagnostics may not spawn processes or cause
    side effects. That rule was written after subprocess-based diagnostics
    broke 20 tests by intercepting pytest's own process spawning. A read-
    only viewer is diagnostics; the rule applies to it.
    """
    import contextlib
    import io

    buffer = io.StringIO()
    try:
        from agent_tick import _print_status
        # BOTH streams. `_print_status` writes to stderr, not stdout —
        # measured, after a stdout-only redirect captured zero characters
        # while the text still reached the terminal. Capturing one stream
        # and reporting "(no output)" would have been a viewer confidently
        # describing silence it had created itself.
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            _print_status(REPO)
    except Exception as exc:  # noqa: BLE001 — reason stated above
        # Named, never swallowed (MIR-077): a viewer that returns "" on an
        # error tells the reader the agent is quiet when in fact nothing was
        # asked.
        partial = buffer.getvalue()
        tail = (chr(10) + "--- partial output ---" + chr(10) + partial) if partial else ""
        return f"status unavailable: {type(exc).__name__}: {str(exc)[:300]}" + tail
    return buffer.getvalue() or "(no output)"


def task_queue() -> dict:
    """Runtime tasks with their status, attempts and last error."""
    result = _read_jsonl(_DATA / "runtime_tasks.jsonl")
    if "rows" not in result:
        return result
    tasks = [_payload(r) for r in result["rows"]]
    by_status: dict[str, int] = {}
    for task in tasks:
        key = str(task.get("status", "?"))
        by_status[key] = by_status.get(key, 0) + 1
    return {
        "store": "runtime_tasks.jsonl",
        "by_status": by_status,
        "unreadable_rows": result["unreadable_rows"],
        "tasks": [
            {k: t.get(k) for k in ("id", "kind", "goal", "status",
                                   "attempts", "last_error", "updated_at")}
            for t in tasks[-20:]
        ],
    }


def approval_inbox() -> dict:
    """Items the agent has put in front of a human and is waiting on.

    A pending item is the agent saying it stopped on purpose. Reading this is
    how an assistant learns what it is blocked on rather than guessing.
    """
    result = _read_jsonl(_DATA / "approval_inbox.jsonl")
    if "rows" not in result:
        return result
    items = [_payload(r) for r in result["rows"]]
    pending = [i for i in items if str(i.get("status", "")) == "pending"]
    return {
        "pending_count": len(pending),
        "total": result["total"],
        "unreadable_rows": result["unreadable_rows"],
        "pending": [
            {k: i.get(k) for k in
             ("id", "operation", "summary", "risk", "created_at")}
            for i in pending[:20]
        ],
    }


def recent_episodes(limit: int = 10) -> dict:
    """The agent's own record of its last runs: question, outcome, verdict
    counts.
    """
    result = _read_jsonl(_DATA / "episodic_memory.jsonl", last=max(1, min(limit, 50)))
    if "rows" not in result:
        return result
    return {
        "total": result["total"],
        "unreadable_rows": result["unreadable_rows"],
        "episodes": [
            {k: _payload(r).get(k) for k in
             ("id", "question", "outcome", "verified_chunks", "unverified_chunks",
              "weak_chunks", "tools_used", "usage_eligible", "created_at")}
            for r in result["rows"]
        ],
    }


def run_journal(limit: int = 40, event_filter: str = "") -> dict:
    """Events from the most recent run log, newest last.

    Streams the file and keeps only the last N MATCHED events in a bounded
    deque. The first version made `_read_jsonl` stream and then called it
    with `last=0`, materialising every row and filtering into a second list
    -- the plumbing was fixed and the one tap that pours 5 MB was left open
    (review of #317). Filtering has to happen DURING the walk, because
    `last` counts matches and the reader cannot know which rows match.
    """
    try:
        logs = sorted(_LOGS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    except OSError as exc:
        return {"error": type(exc).__name__, "store": "logs/", "detail": str(exc)[:200]}
    if not logs:
        return {"error": "missing", "store": "logs/", "detail": "no run logs yet"}
    wanted = {name.strip() for name in event_filter.split(",") if name.strip()}
    keep = max(1, min(limit, 200))
    newest = logs[-1]

    events: collections.deque = collections.deque(maxlen=keep)
    rows = matched = unreadable = 0
    try:
        with newest.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    unreadable += 1
                    continue
                rows += 1
                if wanted and str(row.get("event", "")) not in wanted:
                    continue
                matched += 1
                events.append({
                    "event": row.get("event"),
                    "ts": row.get("ts") or row.get("timestamp"),
                    "payload": row.get("payload"),
                })
    except OSError as exc:
        return {"error": type(exc).__name__, "store": newest.name,
                "detail": str(exc)[:200]}

    return {
        "log_file": newest.name,
        # Two counts, because one was misleading: the total used to be the
        # row count of the whole log whatever the filter, so a caller asking
        # for `error` events saw a large number beside three of them and could
        # not tell an empty filter from an empty run.
        "events_matched": matched,
        "rows_in_log": rows,
        "unreadable_rows": unreadable,
        "events": list(events),
    }


def open_defects() -> dict:
    """Registered defects that are still open, from the agent's own registry."""
    path = REPO / "docs" / "audit" / "MASTER_ISSUE_REGISTRY.md"
    if not path.exists():
        return {"error": "missing", "store": path.name}
    import re
    import sys
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": type(exc).__name__, "store": path.name,
                "detail": str(exc)[:200]}
    scripts_dir = str(REPO / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from registry_tally import _STATUS  # the canonical Status-line parser

    blocks = re.split(r"^### (MIR-\d+) — (.+)$", text, flags=re.MULTILINE)
    out: list[dict] = []
    unparsed: list[str] = []
    for i in range(1, len(blocks), 3):
        status = _STATUS.search(blocks[i + 2])
        if status is None:
            # Reported, never guessed: an entry whose status cannot be read is
            # not silently filed as open — that is the failure just repaired.
            unparsed.append(blocks[i])
            continue
        state = status.group(1)
        if state in ("fixed", "diagnosis_corrected"):
            continue
        out.append({"id": blocks[i], "title": blocks[i + 1].strip()[:120],
                    "status": state})
    result: dict = {"open_count": len(out), "defects": out}
    if unparsed:
        result["unparsed"] = unparsed
    return result

