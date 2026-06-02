"""MVP-13.1 — `read_logs` tool: structured JSONL audit reader.

The agent's primary signal for self-diagnosis is its own JSONL audit
log. This tool exposes recent events to the planner in a safe,
bounded shape so the LLM can answer questions like "what was the last
error?" or "did the previous run hit an approval_deny?" without
needing to grep raw files.

Safety:
  - The tool ONLY reads from `<workspace_root>/logs/`. No traversal.
  - `last_n` is hard-capped to keep payloads bounded.
  - Each returned event passes through `redact_payload` again before
    leaving the tool — the logger already redacts on the way in, so
    this is defence-in-depth.
  - File names must match the trace-id pattern that `TraceLogger`
    writes; anything else is refused so the tool cannot be coerced
    into reading arbitrary `.jsonl` blobs an attacker might drop into
    `logs/`.

Risk: read_only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tools.base import Risk, Tool, require_ascii_identifier


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LAST_N = 50
MAX_LAST_N = 500
MAX_EVENT_FILTER = 20      # how many distinct event names a caller may filter on

# Mirrors `core.loop.new_trace_id` which emits `run_<hex8>`.
# Accepting both hex and a wider safe pattern keeps the tool resilient
# to a future change in the trace-id format.
_TRACE_ID_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_-]+\.jsonl$")


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class ReadLogsTool(Tool):
    """Read the last N events of a session's JSONL audit log."""

    name = "read_logs"
    description = (
        "Read the agent's own JSONL audit log to diagnose errors. "
        "Returns the last N events (default 50, max 500), optionally "
        "filtered by event name (e.g. ['error','replan']). If trace_id "
        "is omitted, reads the most-recently-modified log file. Use "
        "this as the agent's primary self-diagnostic surface. "
        "Risk: read_only."
    )
    risk: Risk = "read_only"

    def __init__(self, workspace_root: Path):
        if not workspace_root.is_dir():
            raise ValueError(
                f"workspace_root must be an existing directory, got {workspace_root}"
            )
        self.workspace_root = workspace_root.resolve()
        self.log_dir = self.workspace_root / "logs"

    def risk_for(self, arguments: dict[str, Any]) -> Risk:  # noqa: ARG002
        return "read_only"

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------

    def run(
        self,
        last_n: int = DEFAULT_LAST_N,
        event_filter: list[str] | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(last_n, int):
            raise ValueError("last_n must be an int")
        if last_n < 1:
            raise ValueError(f"last_n must be >= 1, got {last_n}")
        if last_n > MAX_LAST_N:
            raise ValueError(f"last_n must be <= {MAX_LAST_N}, got {last_n}")

        filter_set: set[str] | None = None
        if event_filter is not None:
            if not isinstance(event_filter, list):
                raise ValueError("event_filter must be a list of strings or None")
            if len(event_filter) > MAX_EVENT_FILTER:
                raise ValueError(
                    f"event_filter too long (max {MAX_EVENT_FILTER}), got {len(event_filter)}"
                )
            cleaned: list[str] = []
            for i, name in enumerate(event_filter):
                if not isinstance(name, str) or not name.strip():
                    raise ValueError(f"event_filter[{i}] must be a non-empty string")
                require_ascii_identifier(name, role=f"read_logs event_filter[{i}]")
                cleaned.append(name)
            filter_set = set(cleaned)

        target_path = self._resolve_log_path(trace_id)
        if target_path is None:
            return {
                "trace_id": trace_id or "",
                "log_file": "",
                "events_returned": 0,
                "total_events": 0,
                "filtered": filter_set is not None,
                "events": [],
                "compensation_plan": _NOOP_PLAN,
            }

        events_all = self._read_jsonl(target_path)
        total = len(events_all)
        if filter_set is not None:
            events_filtered = [e for e in events_all if e.get("event") in filter_set]
        else:
            events_filtered = events_all
        events_recent = events_filtered[-last_n:]

        # Defence-in-depth redact on the way out.
        from core.redaction import redact_payload

        events_safe = [redact_payload(e) for e in events_recent]

        try:
            rel_log = str(target_path.relative_to(self.workspace_root))
        except ValueError:
            rel_log = str(target_path)

        return {
            "trace_id": target_path.stem,
            "log_file": rel_log,
            "events_returned": len(events_safe),
            "total_events": total,
            "filtered": filter_set is not None,
            "events": events_safe,
            "compensation_plan": _NOOP_PLAN,
        }

    # ------------------------------------------------------------------
    # validate_output
    # ------------------------------------------------------------------

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, dict):
            return False, ["read_logs output must be a dict"]
        required = {
            "trace_id", "log_file", "events_returned", "total_events",
            "filtered", "events", "compensation_plan",
        }
        missing = required - output.keys()
        if missing:
            return False, [f"missing keys: {sorted(missing)}"]

        if not isinstance(output["events"], list):
            return False, ["events must be a list"]
        if not isinstance(output["total_events"], int) or output["total_events"] < 0:
            return False, ["total_events must be a non-negative int"]
        if not isinstance(output["events_returned"], int) or output["events_returned"] < 0:
            return False, ["events_returned must be a non-negative int"]
        if output["events_returned"] > output["total_events"] and output["filtered"] is False:
            return False, ["events_returned > total_events with no filter"]
        if not isinstance(output["filtered"], bool):
            return False, ["filtered must be a bool"]
        if not isinstance(output["compensation_plan"], dict):
            return False, ["compensation_plan must be a dict"]
        return True, []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_log_path(self, trace_id: str | None) -> Path | None:
        # Validate trace_id BEFORE checking the logs dir so a malformed
        # trace_id raises in a deterministic way regardless of whether
        # the workspace happens to have any logs yet.
        if trace_id is not None:
            require_ascii_identifier(trace_id, role="read_logs trace_id")
            # Reject obvious traversal shapes here (we don't yet know
            # the log_dir might exist downstream).
            if "/" in trace_id or "\\" in trace_id or ".." in trace_id:
                raise PermissionError(
                    f"trace_id {trace_id!r} contains path separators or "
                    f"parent-traversal; refused"
                )
            if not _TRACE_ID_FILENAME_RE.match(f"{trace_id}.jsonl"):
                raise PermissionError(
                    f"trace_id {trace_id!r} produced an unsafe filename"
                )

        if not self.log_dir.is_dir():
            return None
        if trace_id is not None:
            candidate = self.log_dir / f"{trace_id}.jsonl"
            # Re-resolve and re-check containment to prevent symlink games.
            resolved = candidate.resolve()
            try:
                resolved.relative_to(self.log_dir.resolve())
            except ValueError:
                raise PermissionError(
                    f"trace_id {trace_id!r} resolves outside the logs/ directory"
                ) from None
            return resolved if resolved.is_file() else None

        # No trace_id given — pick the most recently modified .jsonl.
        candidates = [p for p in self.log_dir.iterdir() if p.suffix == ".jsonl" and p.is_file()]
        if not candidates:
            return None
        candidates.sort(key=lambda p: p.stat().st_mtime)
        return candidates[-1]

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    # Skip malformed lines silently rather than abort —
                    # the agent should see what's parseable, even if
                    # someone hand-edited the file.
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
        return out


_NOOP_PLAN = {
    "id": "noop",
    "actions": [{"kind": "noop", "description": "read_logs is read-only"}],
    "tool_name": "read_logs",
    "description": "read_logs makes no changes; no rollback needed",
}
