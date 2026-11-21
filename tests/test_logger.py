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

from pathlib import Path

from core.logger import TraceLogger

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
