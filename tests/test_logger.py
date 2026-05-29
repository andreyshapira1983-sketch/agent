"""TraceLogger — JSONL serialisation, redaction, event marker dispatch.

The logger is the audit log. Every other test trusts that what it sees in
the JSONL file matches what the loop emitted. These tests pin the contract:
  - Every record is one valid JSON line with `ts`, `trace_id`, `event`.
  - Pydantic models serialise via `.model_dump(mode='json')`.
  - Strings inside payloads + extra are passed through `redact_payload`,
    so no raw credential ever lands on disk.
  - Pretty-printing to stderr also sees the redacted view.
  - Close() flushes and the file handle is releasable.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from core.logger import TraceLogger


# A small Pydantic model so we can exercise the model_dump path.
class _Sample(BaseModel):
    id: str
    decision: str
    note: str = ""


SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123"


def _read_lines(path: Path) -> list[dict]:
    out: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ============================================================
# Basic JSONL output
# ============================================================

class TestRecordShape:
    def test_each_log_call_writes_one_line(self, workspace: Path):
        logger = TraceLogger(
            trace_id="run_test", log_dir=workspace / "logs", verbose=False
        )
        try:
            logger.log("observe", {"x": 1})
            logger.log("plan", {"steps": 0})
            logger.log("respond", {"chars": 42})
        finally:
            logger.close()

        path = workspace / "logs" / "run_test.jsonl"
        records = _read_lines(path)
        assert len(records) == 3
        assert [r["event"] for r in records] == ["observe", "plan", "respond"]

    def test_record_contains_ts_trace_id_event(self, workspace: Path):
        logger = TraceLogger(
            trace_id="run_xyz", log_dir=workspace / "logs", verbose=False
        )
        try:
            logger.log("observe", {"q": "hello"})
        finally:
            logger.close()

        record = _read_lines(workspace / "logs" / "run_xyz.jsonl")[0]
        assert record["trace_id"] == "run_xyz"
        assert record["event"] == "observe"
        # Timestamp is ISO-8601 with timezone — must parse.
        from datetime import datetime
        datetime.fromisoformat(record["ts"])

    def test_no_payload_no_payload_field(self, workspace: Path):
        logger = TraceLogger(
            trace_id="empty", log_dir=workspace / "logs", verbose=False
        )
        try:
            logger.log("session_start")
        finally:
            logger.close()

        record = _read_lines(workspace / "logs" / "empty.jsonl")[0]
        assert "payload" not in record

    def test_extra_kwargs_land_in_record(self, workspace: Path):
        logger = TraceLogger(
            trace_id="extra", log_dir=workspace / "logs", verbose=False
        )
        try:
            logger.log("plan", {"steps": 1}, attempt=2, source="cli")
        finally:
            logger.close()

        record = _read_lines(workspace / "logs" / "extra.jsonl")[0]
        assert record["extra"] == {"attempt": 2, "source": "cli"}


# ============================================================
# Pydantic model serialisation
# ============================================================

class TestPydanticSerialisation:
    def test_basemodel_payload_becomes_json_dict(self, workspace: Path):
        logger = TraceLogger(
            trace_id="pyd", log_dir=workspace / "logs", verbose=False
        )
        try:
            logger.log("policy", _Sample(id="p1", decision="allow"))
        finally:
            logger.close()

        record = _read_lines(workspace / "logs" / "pyd.jsonl")[0]
        # model_dump(mode="json") returns a real dict; logger emits it
        # as a nested JSON object, not a string.
        assert isinstance(record["payload"], dict)
        assert record["payload"]["id"] == "p1"
        assert record["payload"]["decision"] == "allow"

    def test_nested_list_of_models_is_walked(self, workspace: Path):
        logger = TraceLogger(
            trace_id="lst", log_dir=workspace / "logs", verbose=False
        )
        try:
            logger.log(
                "batch",
                [_Sample(id="a", decision="allow"), _Sample(id="b", decision="deny")],
            )
        finally:
            logger.close()

        record = _read_lines(workspace / "logs" / "lst.jsonl")[0]
        assert isinstance(record["payload"], list)
        assert record["payload"][0]["id"] == "a"
        assert record["payload"][1]["decision"] == "deny"


# ============================================================
# Redaction in the logger
# ============================================================

class TestLoggerRedaction:
    """Defence-in-depth. Even if a payload field accidentally carries a
    credential (e.g. raw tool output before the loop's safety pipeline
    runs), the logger MUST scrub it on the way to disk."""

    def test_secret_in_string_payload_is_redacted(self, workspace: Path):
        logger = TraceLogger(
            trace_id="red1", log_dir=workspace / "logs", verbose=False
        )
        try:
            logger.log("tool_result", {"output": f"key={SECRET} done"})
        finally:
            logger.close()

        text = (workspace / "logs" / "red1.jsonl").read_text(encoding="utf-8")
        assert SECRET not in text
        assert "[REDACTED:" in text

    def test_secret_in_nested_payload_is_redacted(self, workspace: Path):
        logger = TraceLogger(
            trace_id="red2", log_dir=workspace / "logs", verbose=False
        )
        try:
            logger.log(
                "tool_result",
                {
                    "outer": {"deep": [f"token: {SECRET}"], "ok": "fine"},
                    "list": [{"k": SECRET}],
                },
            )
        finally:
            logger.close()

        text = (workspace / "logs" / "red2.jsonl").read_text(encoding="utf-8")
        assert SECRET not in text
        assert text.count("[REDACTED") >= 2  # two nested positions

    def test_secret_in_extra_kwargs_is_redacted(self, workspace: Path):
        # Defence-in-depth: `extra` is the looser API. It must redact too.
        logger = TraceLogger(
            trace_id="red3", log_dir=workspace / "logs", verbose=False
        )
        try:
            logger.log("policy", {"id": "p1"}, leak=SECRET)
        finally:
            logger.close()

        text = (workspace / "logs" / "red3.jsonl").read_text(encoding="utf-8")
        assert SECRET not in text

    def test_pretty_print_to_stderr_is_also_redacted(
        self, workspace: Path, capsys
    ):
        logger = TraceLogger(
            trace_id="stderr", log_dir=workspace / "logs", verbose=True
        )
        try:
            logger.log("tool_result", {"output": f"key={SECRET} done"})
        finally:
            logger.close()

        captured = capsys.readouterr()
        # Logger prints to stderr; the redacted view must apply there too —
        # stderr is a leak surface (terminal, log scrapers, …).
        assert SECRET not in captured.err
        assert "[REDACTED" in captured.err


# ============================================================
# Event marker dispatch (pretty-print)
# ============================================================

class TestEventMarkers:
    def test_known_event_uses_short_marker(self, workspace: Path, capsys):
        logger = TraceLogger(
            trace_id="mk", log_dir=workspace / "logs", verbose=True
        )
        try:
            logger.log("observe")
            logger.log("policy")
            logger.log("tool_result", status="success")
        finally:
            logger.close()

        err = capsys.readouterr().err
        # Each known event has a 3-letter marker in the pretty-print.
        assert "[OBS]" in err
        assert "[POL]" in err
        assert "[RES]" in err

    def test_unknown_event_falls_back_to_uppercase_prefix(
        self, workspace: Path, capsys
    ):
        logger = TraceLogger(
            trace_id="mk2", log_dir=workspace / "logs", verbose=True
        )
        try:
            logger.log("replan", {"attempt": 1})  # not in the static map
        finally:
            logger.close()

        err = capsys.readouterr().err
        # Falls back to `event.upper()[:4]` → "REPL" for `replan`.
        assert "[REPL]" in err


# ============================================================
# Close + file-handle hygiene
# ============================================================

class TestClose:
    def test_close_allows_file_reopen(self, workspace: Path):
        logger = TraceLogger(
            trace_id="hc", log_dir=workspace / "logs", verbose=False
        )
        logger.log("session_start")
        logger.close()

        # On Windows, an unclosed handle would block reopening for write.
        # If close() works, a fresh logger on the SAME path appends cleanly.
        logger2 = TraceLogger(
            trace_id="hc", log_dir=workspace / "logs", verbose=False
        )
        try:
            logger2.log("respond", {"chars": 1})
        finally:
            logger2.close()

        records = _read_lines(workspace / "logs" / "hc.jsonl")
        assert [r["event"] for r in records] == ["session_start", "respond"]
