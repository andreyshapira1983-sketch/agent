"""Reflection's recency gate had no witness, and the live cost would be silent.

Found by mutation 2026-08-20: flipping `reverse=True` to `reverse=False` in
`_load_recent_logs` survived the entire suite.

The engine sorts `*.jsonl` by mtime and keeps `config.max_logs` of them.
Reversed, the slice returns the OLDEST files instead of the newest. In
production that is decisive rather than cosmetic: `core/autonomous_runtime.py`
always passes a bounded rotated window (20, 40, 30, 60) and the live log
directory holds well over a thousand files. The agent would re-read its first
few dozen runs forever, deriving lessons about failures fixed months ago and
never seeing anything that just happened — while the report still says
`logs_scanned=20, events_scanned=N, patterns_found=[...]`, because
`logs_scanned` counts the files AFTER truncation. Plausible numbers, dead
signal; the same failure class `docs/CODE_NOTES.md` already records twice.

Why nothing caught it: every fixture in the suite writes exactly ONE log file,
so the window and its ordering are only ever exercised where both are no-ops.
The word `max_logs` did not appear in a single test file in the repo.

The invariant is about recency, not about a number: with more logs than the
window, the events must come from the newest files.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from core.reflection import ReflectionConfig, ReflectionEngine


def _engine(log_dir: Path, workspace: Path) -> ReflectionEngine:
    return ReflectionEngine(
        workspace=workspace,
        persistent_memory=None,
        llm=None,
        log_dir=log_dir,
    )


def _seed(log_dir: Path, count: int) -> list[str]:
    """`count` log files, one marker event each, mtimes strictly increasing."""
    log_dir.mkdir(parents=True, exist_ok=True)
    markers = []
    for i in range(count):
        marker = f"marker-{i}"
        path = log_dir / f"run_{i}.jsonl"
        path.write_text(
            json.dumps({"trace_id": marker, "event": "tool_call",
                        "payload": {"id": marker, "tool_name": "probe"}}) + "\n",
            encoding="utf-8",
        )
        os.utime(path, (1_700_000_000 + i * 60, 1_700_000_000 + i * 60))
        markers.append(marker)
    return markers


def test_the_window_keeps_the_newest_logs(workspace: Path) -> None:
    log_dir = workspace / "logs"
    markers = _seed(log_dir, 5)
    warnings: list[str] = []

    events, scanned = _engine(log_dir, workspace)._load_recent_logs(
        ReflectionConfig(max_logs=2), warnings
    )

    seen = {e.get("trace_id") for e in events}
    assert scanned == 2
    assert seen == set(markers[-2:]), (
        f"the window returned {sorted(seen)} — reflection is reading its "
        f"oldest runs, not its newest (expected {markers[-2:]})"
    )


def test_a_window_wider_than_the_directory_reads_everything(
    workspace: Path,
) -> None:
    """Boundary pin: the truncation must not fire when there is nothing to
    truncate, or the fixture below would pass for the wrong reason."""
    log_dir = workspace / "logs"
    markers = _seed(log_dir, 3)
    warnings: list[str] = []

    events, scanned = _engine(log_dir, workspace)._load_recent_logs(
        ReflectionConfig(max_logs=10), warnings
    )

    assert scanned == 3
    assert {e.get("trace_id") for e in events} == set(markers)
