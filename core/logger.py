"""Structured JSONL logger for the agent loop.

Every cycle phase emits one JSON line to both stdout (info+) and
logs/<trace_id>.jsonl. This is what makes the agent debuggable.

Hard rule (§7 / MVP-7): NO raw secret ever appears on a log line.
Every payload — and every `extra` kwarg — is fed through
`core.redaction.redact_payload` before serialisation. Adding new event
types does not require remembering to redact them; the logger does it
unconditionally.
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from core.redaction import redact_payload


class TraceLogger:
    def __init__(self, trace_id: str, log_dir: Path | str = "logs", verbose: bool = True):
        self.trace_id = trace_id
        self.verbose = verbose
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.log_dir / f"{trace_id}.jsonl"
        self._fh = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()  # serialises concurrent writes from parallel step threads

    def log(self, event: str, payload: Any = None, **extra: Any) -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "trace_id": self.trace_id,
            "event": event,
        }
        if payload is not None:
            record["payload"] = redact_payload(_serialize(payload))
        if extra:
            record["extra"] = redact_payload(_serialize(extra))
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
        if self.verbose:
            # Pretty-print also sees the redacted view — stderr is a leak
            # surface and must not be a back door for raw credentials.
            self._pretty_print(event, record.get("payload"), record.get("extra") or {})

    def _pretty_print(self, event: str, payload: Any, extra: dict[str, Any]) -> None:
        marker = _event_marker(event)
        head = f"[{marker}] {event}"
        if isinstance(payload, BaseModel):
            summary = _summarize_model(payload)
        elif isinstance(payload, dict):
            summary = ", ".join(f"{k}={_short(v)}" for k, v in payload.items())
        elif payload is None:
            summary = ""
        else:
            summary = _short(payload)
        line = f"{head}  {summary}" if summary else head
        if extra:
            line += "  " + " ".join(f"{k}={_short(v)}" for k, v in extra.items())
        print(line, file=sys.stderr)

    def close(self) -> None:
        self._fh.close()


def _serialize(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, (list, tuple)):
        return [_serialize(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def _short(value: Any, limit: int = 80) -> str:
    text = str(value).replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _summarize_model(model: BaseModel) -> str:
    data = model.model_dump(mode="python")
    keys = list(data.keys())
    priority_keys = [k for k in ("id", "status", "decision", "tool_name", "description") if k in keys]
    other_keys = [k for k in keys if k not in priority_keys][:2]
    parts = [f"{k}={_short(data[k], 40)}" for k in priority_keys + other_keys]
    return " ".join(parts)


def _event_marker(event: str) -> str:
    return {
        "observe": "OBS",
        "interpret": "INT",
        "plan": "PLN",
        "act": "ACT",
        "policy": "POL",
        "tool_call": "CALL",
        "tool_result": "RES",
        "verify": "VER",
        "respond": "OUT",
        "error": "ERR",
    }.get(event, event.upper()[:4])
