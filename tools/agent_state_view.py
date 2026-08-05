#!/usr/bin/env python3
"""Read-only view of this agent's own state, for whoever is watching it.

The operator's question, 2026-08-05: can the autonomous agent and an assistant
coordinate instead of losing each other between sessions? MCP runs
client → server, and Claude Code is a CLIENT — so the working direction is the
reverse of the intuitive one: the AGENT exposes a server, and the assistant
connects to it.

## Read-only, and that is a design decision rather than a first version

Every tool here reads a store and returns what it found. Nothing enqueues,
approves, writes or deletes. Two reasons, both worth keeping:

**The loop needs a brake.** An autonomous agent that can ask an assistant which
can edit code and run commands is a cycle with no human in it. This repository
already owns the right gate — `ActuationGateway` and the approval inbox — and
a write path added here would sit beside that gate rather than behind it.

**The interface is not knowable yet.** What the agent will actually want to ask
is unmeasured. Designing the answer channel before the first real question is
how you build the wrong one; the same mistake, in miniature, as guessing a
field name instead of reading it.

## Failure is reported, never swallowed (MIR-077)

A store that will not open returns `{"error": ...}` naming the store and the
exception type. An empty list and an unreadable file are different facts, and
a reader who cannot tell them apart is worse off than one who gets an error.

No `mcp` import here on purpose. That package brings 18 direct dependencies —
starlette, uvicorn, sse-starlette, websockets, pyjwt[crypto], typer, rich —
and this repository pins and locks everything it ships, with an SBOM. A
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
    """Rows of a JSONL store, or a named error -- never a silent empty list.

    Streams line by line. `read_text().splitlines()` held the whole file plus
    the whole list of lines in memory at once, and the largest journal in this
    workspace is already 5 MB across 292 files -- a viewer that dies reading
    the log is worse than no viewer, because it fails exactly when the run was
    long enough to be worth looking at.

    `last` is served by a bounded `deque`, so asking for the final 40 events of
    a million-line journal costs 40 rows of memory rather than a million.
    `total` still counts every row, because "the last 40 of 12 000" and "the
    last 40 of 40" are different facts about the same answer.
    """
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

    Calls the agent's own `_print_status` in-process and captures what it
    writes, rather than shelling out to `agent_tick.py --status`. Two reasons,
    and the second is the repository's own rule:

    It is the SAME code, so this cannot drift into a second opinion about the
    agent's state -- the failure mode a re-implementation would have.

    And notebook section 7: diagnostics may not spawn processes or cause side
    effects. That rule was written after subprocess-based diagnostics broke 20
    tests by intercepting pytest's own process spawning. A read-only viewer is
    diagnostics; the rule applies to it.
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
    except Exception as exc:
        # Named, never swallowed (MIR-077): a viewer that returns "" on an
        # error tells the reader the agent is quiet when in fact nothing was
        # asked.
        partial = buffer.getvalue()
        tail = (chr(10) + "--- partial output ---" + chr(10) + partial) if partial else ""
        return f"status unavailable: {type(exc).__name__}: {str(exc)[:300]}" + tail
    return buffer.getvalue() or "(no output)"


def task_queue() -> dict:
    """Runtime tasks with their status, attempts and last error.

    The queue is how the daemon is told to do anything, so what is pending here
    is what the agent believes it has been asked for.
    """
    # BOTH stores, named, because this repository has two and they are not the
    # same queue: `agent_tick.py` writes `task_queue.jsonl` while `app/bootstrap`
    # and the health commands read `runtime_tasks.jsonl`. Returning one would
    # let a reader conclude "nothing queued" while the other side is busy.
    # Found while writing this server, 2026-08-05; not fixed here — a merge is
    # a decision about which side is authoritative, not a display detail.
    out: dict[str, Any] = {}
    for label, name in (("daemon", "task_queue.jsonl"),
                        ("repl_and_health", "runtime_tasks.jsonl")):
        result = _read_jsonl(_DATA / name)
        if "rows" not in result:
            out[label] = result
            continue
        tasks = [_payload(r) for r in result["rows"]]
        by_status: dict[str, int] = {}
        for task in tasks:
            key = str(task.get("status", "?"))
            by_status[key] = by_status.get(key, 0) + 1
        out[label] = {
            "store": name,
            "by_status": by_status,
            "unreadable_rows": result["unreadable_rows"],
            "tasks": [
                {k: t.get(k) for k in ("id", "kind", "goal", "status",
                                       "attempts", "last_error", "updated_at")}
                for t in tasks[-20:]
            ],
        }
    return out


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
    """The agent's own record of its last runs: question, outcome, verdict counts.

    `verified_chunks` here is the number MIR-060 is about — it counts claims
    whose citation resolved, which since direction (b) also means the
    arithmetic did not refute them.
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

    `event_filter` is a comma-separated list of event names; empty means all.
    This is where a failure now leaves a trace — before MIR-077 closed, 46
    handlers in `core/` swallowed one without writing anything here.
    """
    try:
        logs = sorted(_LOGS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    except OSError as exc:
        return {"error": type(exc).__name__, "store": "logs/", "detail": str(exc)[:200]}
    if not logs:
        return {"error": "missing", "store": "logs/", "detail": "no run logs yet"}
    wanted = {name.strip() for name in event_filter.split(",") if name.strip()}
    result = _read_jsonl(logs[-1])
    if "rows" not in result:
        return result
    events = [r for r in result["rows"]
              if not wanted or str(r.get("event", "")) in wanted]
    return {
        "log_file": logs[-1].name,
        # Two counts, because one was misleading: `events_total` used to
        # report the file's row count even when a filter was given, so a
        # caller asking for `error` events saw "events_total: 12000" beside
        # three of them and could not tell an empty filter from an empty
        # run (review of #316). `rows_in_log` keeps the raw figure, which
        # is what makes the match count meaningful at all.
        "events_matched": len(events),
        "rows_in_log": result["total"],
        "unreadable_rows": result["unreadable_rows"],
        "events": [
            {"event": e.get("event"), "ts": e.get("ts") or e.get("timestamp"),
             "payload": e.get("payload")}
            for e in events[-max(1, min(limit, 200)):]
        ],
    }


def open_defects() -> dict:
    """Registered defects that are still open, from the agent's own registry.

    `docs/audit/MASTER_ISSUE_REGISTRY.md` is the single owner of defect status
    in this repository. Reading it here means an assistant and the agent argue
    from the same list instead of each keeping its own.
    """
    path = REPO / "docs" / "audit" / "MASTER_ISSUE_REGISTRY.md"
    if not path.exists():
        return {"error": "missing", "store": path.name}
    import re
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": type(exc).__name__, "store": path.name,
                "detail": str(exc)[:200]}
    blocks = re.split(r"^### (MIR-\d+) — (.+)$", text, flags=re.MULTILINE)
    out: list[dict] = []
    for i in range(1, len(blocks), 3):
        status = re.search(r"\*\*Status:\*\*\s*`?(\w+)`?", blocks[i + 2])
        state = status.group(1) if status else "?"
        if state in ("fixed", "diagnosis_corrected"):
            continue
        out.append({"id": blocks[i], "title": blocks[i + 1].strip()[:120],
                    "status": state})
    return {"open_count": len(out), "defects": out}

