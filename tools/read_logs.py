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

# Longest a single payload field may be on the way out, in JSON characters.
# Measured 2026-09-05 (exam turn 36): the agent read its own trace without a
# filter, 217 events came back as 1.24 MB — each earlier `read_logs` result
# was nested whole inside its `tool_result.output` — and the evidence budget
# kept ~12 000 characters, none of them the planner warnings it was after.
# A field over the cap is replaced by a marked preview; the event itself and
# every short field stay intact.
MAX_FIELD_CHARS = 4_000

# Accepting a wider safe pattern than `core.ids.new_trace_id` currently emits
# keeps an explicitly-passed trace_id resilient to a format change.
_TRACE_ID_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_-]+\.jsonl$")

# Which files in `logs/` TraceLogger itself wrote. `core.ids.new_trace_id` emits
# `trace_<hex>`; `run_<hex>` is the pre-2026-08-10 name and is still on disk.
# Everything else there (checkpoints_*, daemon_tick, ad-hoc captures) is another
# store, and picking one to diagnose from reports the wrong subsystem.
_SESSION_LOG_STEM_RE = re.compile(r"^(?:trace|run)_[0-9a-zA-Z]+$")

# Сколько прошлых трасс просматривается в поисках запрошенных событий, прежде
# чем честно сказать «в недавней истории этого нет». Ограничение держит вызов
# ограниченным по работе: дальше вглубь — это уже археология, и ей положено
# идти через явный trace_id.
MAX_TRACE_SCAN = 15


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
        "is omitted, reads the most recent PAST session log — never the one "
        "this session is writing — and when an event_filter is given, the "
        "most recent past log that CONTAINS such events. Every result names "
        "the current session's log in `live_trace_id`; pass it as trace_id "
        "to read this session's earlier turns. events_returned=0 with "
        "traces_searched>1 means no such events exist in recent history at "
        f"all, not just in one file. Payload fields longer than {MAX_FIELD_CHARS} "
        "chars are cut to a marked preview (`_truncated`), so filter by event "
        "name for what you need rather than reading a whole trace. Use this "
        "as the agent's primary self-diagnostic surface. Risk: read_only."
    )
    risk: Risk = "read_only"

    def __init__(self, workspace_root: Path, live_trace_id: str | None = None):
        if not workspace_root.is_dir():
            raise ValueError(
                f"workspace_root must be an existing directory, got {workspace_root}"
            )
        self.workspace_root = workspace_root.resolve()
        self.log_dir = self.workspace_root / "logs"
        # The session log this agent is appending to right now. Excluded from the
        # no-trace_id default, because a run cannot read its own outcome.
        self.live_trace_id = live_trace_id

    def risk_for(self, arguments: dict[str, Any]) -> Risk:
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

        # Свежесть — не то же самое, что уместность. Живой случай 2026-08-15:
        # автономный прогон спросил `event_filter=['error']`, получил самую
        # свежую прошлую трассу — пять служебных строк сессии, где оператор
        # кликал по очереди одобрений, — и встал: «нужные для диагностики
        # события недоступны». Ошибки при этом лежали в соседних трассах.
        # Поэтому при фильтре и без явного адреса выбирается новейшая трасса,
        # СОДЕРЖАЩАЯ запрошенное; `traces_searched` отличает «нет в этой» от
        # «нет в недавней истории вообще». Явный trace_id — адрес, и ответ
        # обязан быть про него, пустой или нет.
        # Зачем: docs/CODE_NOTES.md, «Recency is not relevance».
        traces_searched = 1
        if trace_id is None and filter_set is not None:
            target_path, events_all, traces_searched = self._newest_trace_with(filter_set)
        else:
            target_path = self._resolve_log_path(trace_id)
            events_all = self._read_jsonl(target_path) if target_path else []
        if target_path is None:
            return {
                "trace_id": trace_id or "",
                "log_file": "",
                "events_returned": 0,
                "total_events": 0,
                "filtered": filter_set is not None,
                "events": [],
                "is_live_session": False,
                "skipped_live": False,
                "traces_searched": traces_searched,
                "live_trace_id": self.live_trace_id or "",
                "fields_truncated": 0,
                "compensation_plan": _NOOP_PLAN,
            }
        total = len(events_all)
        if filter_set is not None:
            events_filtered = [e for e in events_all if e.get("event") in filter_set]
        else:
            events_filtered = events_all
        events_recent = events_filtered[-last_n:]

        # Defence-in-depth redact on the way out.
        from core.redaction import redact_payload

        events_safe = [redact_payload(e) for e in events_recent]
        events_safe, fields_truncated = _bound_events(events_safe)

        try:
            rel_log = str(target_path.relative_to(self.workspace_root))
        except ValueError:
            rel_log = str(target_path)

        is_live = bool(self.live_trace_id) and target_path.stem == self.live_trace_id
        skipped_live = bool(self.live_trace_id) and trace_id is None and not is_live
        result: dict[str, Any] = {
            "trace_id": target_path.stem,
            "log_file": rel_log,
            "events_returned": len(events_safe),
            "total_events": total,
            "filtered": filter_set is not None,
            "events": events_safe,
            # Which run this diagnosis is about. Reading the caller's own
            # unfinished trace answers a different question than it looks like.
            "is_live_session": is_live,
            "skipped_live": skipped_live,
            # >1 при пустых events означает: запрошенных событий нет во всей
            # просмотренной истории, а не только в возвращённой трассе.
            "traces_searched": traces_searched,
            # The one id the caller cannot look up anywhere else. Measured
            # 2026-09-05: asked for "the command you ran last turn", the
            # agent read the newest OTHER file — its own subagent's trace —
            # and reported it as its session. A default that hides the live
            # log must at least say which log it hid.
            "live_trace_id": self.live_trace_id or "",
            # How many payload fields were cut to MAX_FIELD_CHARS. Non-zero
            # says: the whole value is in the file, not in this result.
            "fields_truncated": fields_truncated,
            "compensation_plan": _NOOP_PLAN,
        }
        if skipped_live:
            result["hint"] = (
                f"This is a PAST session's log. The current session writes "
                f"{self.live_trace_id}; pass trace_id={self.live_trace_id!r} "
                "to read this session's earlier turns."
            )
        return result

    # ------------------------------------------------------------------
    # validate_output
    # ------------------------------------------------------------------

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, dict):
            return False, ["read_logs output must be a dict"]
        required = {
            "trace_id", "log_file", "events_returned", "total_events",
            "filtered", "events", "is_live_session", "skipped_live",
            "traces_searched", "compensation_plan",
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
        if not isinstance(output["traces_searched"], int) or output["traces_searched"] < 0:
            return False, ["traces_searched must be a non-negative int"]
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
            # Голый идентификатор — законный адрес: живой отказ 2026-08-28
            # (раунд 7 допроса) — агент передал hex собственной трассы и
            # получил «файла нет, 0 событий». TraceLogger пишет стемы
            # `trace_<hex>`/`run_<hex>`; точное имя остаётся первым, префиксы
            # — запасным ходом, проверки содержания — те же для каждого.
            stems = [trace_id]
            if not trace_id.startswith(("trace_", "run_")):
                stems += [f"trace_{trace_id}", f"run_{trace_id}"]
            for stem in stems:
                candidate = self.log_dir / f"{stem}.jsonl"
                # Re-resolve and re-check containment to prevent symlink games.
                resolved = candidate.resolve()
                try:
                    resolved.relative_to(self.log_dir.resolve())
                except ValueError:
                    raise PermissionError(
                        f"trace_id {trace_id!r} resolves outside the logs/ "
                        f"directory"
                    ) from None
                if resolved.is_file():
                    return resolved
            return None

        # No trace_id given — the most recent session log that can hold an
        # answer. `logs/` also holds checkpoints and other stores; those are
        # never event logs. The live session's own trace is the last resort:
        # while it is being written it cannot contain this run's outcome.
        candidates = [
            p for p in self.log_dir.iterdir()
            if p.suffix == ".jsonl" and p.is_file()
            and _SESSION_LOG_STEM_RE.match(p.stem)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda p: p.stat().st_mtime)
        if self.live_trace_id:
            past = [p for p in candidates if p.stem != self.live_trace_id]
            if past:
                return past[-1]
        return candidates[-1]

    def _newest_trace_with(
        self, filter_set: set[str]
    ) -> tuple[Path | None, list[dict[str, Any]], int]:
        """Новейшая ПРОШЛАЯ трасса, где запрошенные события есть."""
        candidates = [
            p for p in (self.log_dir.iterdir() if self.log_dir.is_dir() else [])
            if p.suffix == ".jsonl" and p.is_file()
            and _SESSION_LOG_STEM_RE.match(p.stem)
            and p.stem != (self.live_trace_id or "")
        ]
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        candidates = candidates[:MAX_TRACE_SCAN]
        if not candidates:
            fallback = self._resolve_log_path(None)
            events = self._read_jsonl(fallback) if fallback else []
            return fallback, events, 1 if fallback else 0

        newest_events: list[dict[str, Any]] | None = None
        for i, path in enumerate(candidates):
            events = self._read_jsonl(path)
            if newest_events is None:
                newest_events = events
            if any(e.get("event") in filter_set for e in events):
                return path, events, i + 1
        return candidates[0], newest_events or [], len(candidates)

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


def _bound_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Cut every payload field longer than MAX_FIELD_CHARS to a marked preview.

    Returns the bounded events and the number of fields cut. A cut field
    becomes `{"_truncated": True, "chars": N, "preview": "<json…>"}` so a
    reader can tell a short value from a shortened one.
    """
    bounded: list[dict[str, Any]] = []
    cut = 0
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            bounded.append(event)
            continue
        new_payload: dict[str, Any] = {}
        for key, value in payload.items():
            text = json.dumps(value, ensure_ascii=False, default=str)
            if len(text) <= MAX_FIELD_CHARS:
                new_payload[key] = value
                continue
            cut += 1
            new_payload[key] = {
                "_truncated": True,
                "chars": len(text),
                "preview": text[:MAX_FIELD_CHARS],
            }
        bounded.append({**event, "payload": new_payload})
    return bounded, cut
