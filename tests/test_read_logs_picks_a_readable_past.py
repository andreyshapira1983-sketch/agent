"""The self-diagnostic tool must not hand the agent its own live trace.

Background: docs/CODE_NOTES.md, "read_logs and the live trace".
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from tools.read_logs import ReadLogsTool


def _write(path: Path, events: list[dict], mtime: float) -> None:
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    os.utime(path, (mtime, mtime))


def test_the_live_session_trace_is_not_the_default(tmp_path: Path):
    """Asked what went wrong, the tool reaches for a run that already ended."""
    logs = tmp_path / "logs"
    logs.mkdir()
    now = time.time()
    _write(logs / "trace_old.jsonl",
           [{"event": "error", "detail": "the failure worth finding"}], now - 60)
    _write(logs / "trace_live.jsonl", [{"event": "session_start"}], now)

    out = ReadLogsTool(tmp_path, live_trace_id="trace_live").run()

    assert out["trace_id"] == "trace_old", (
        "the tool returned the trace of the run asking the question; a failure "
        "from an earlier run is invisible by construction"
    )
    assert out["skipped_live"] is True
    assert out["is_live_session"] is False
