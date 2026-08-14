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


def test_the_live_trace_is_used_when_it_is_all_there_is(tmp_path: Path):
    """No history yet is not a reason to return nothing — but say what it is."""
    logs = tmp_path / "logs"
    logs.mkdir()
    _write(logs / "trace_live.jsonl", [{"event": "session_start"}], time.time())

    out = ReadLogsTool(tmp_path, live_trace_id="trace_live").run()

    assert out["trace_id"] == "trace_live"
    assert out["is_live_session"] is True
    assert out["skipped_live"] is False


def test_a_checkpoint_file_is_never_mistaken_for_an_event_log(tmp_path: Path):
    """`logs/` is a mixed store; only TraceLogger's own files are event logs."""
    logs = tmp_path / "logs"
    logs.mkdir()
    now = time.time()
    _write(logs / "trace_real.jsonl", [{"event": "error"}], now - 60)
    for name in ("checkpoints_trace_real.jsonl", "daemon_tick.jsonl",
                 "live-run-7f3.jsonl"):
        _write(logs / name, [{"event": "checkpoint"}], now)

    out = ReadLogsTool(tmp_path).run()

    assert out["trace_id"] == "trace_real", (
        f"picked {out['trace_id']!r} — a non-event file won the mtime race, so "
        "the agent diagnosed itself from checkpoints"
    )


def test_the_legacy_run_prefix_is_still_readable(tmp_path: Path):
    """99 `trace_` files and 278 older `run_` ones share `logs/`; both count."""
    logs = tmp_path / "logs"
    logs.mkdir()
    _write(logs / "run_legacy.jsonl", [{"event": "error"}], time.time())

    assert ReadLogsTool(tmp_path).run()["trace_id"] == "run_legacy"
