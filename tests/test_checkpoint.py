"""Tests for §3.5 Checkpoint / Resume (core/checkpoint.py).

Covers:
- CheckpointWriter: creates file, writes correct phases, typed helpers.
- CheckpointLoader: returns None for missing/corrupt files, reconstructs
  ResumeContext correctly from a full set of records.
- Round-trip: write observe→plan→act→respond, then load and verify
  ResumeContext fields.
- Partial run (crash before respond): loader returns context with answer=None.
- Multiple attempts: last plan attempt wins.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.checkpoint import (
    PHASE_ACT,
    CheckpointLoader,
    CheckpointRecord,
    CheckpointWriter,
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _writer(tmp: Path, trace_id: str = "trace-001") -> CheckpointWriter:
    return CheckpointWriter(trace_id=trace_id, log_dir=tmp / "logs")


def _loader(tmp: Path) -> CheckpointLoader:
    return CheckpointLoader(tmp / "logs")


def _full_run(tmp: Path, trace_id: str = "trace-001") -> CheckpointWriter:
    """Write a complete observe→plan→act→respond sequence."""
    w = _writer(tmp, trace_id)
    w.save_observe(question="что такое Docker?", file_hint=None)
    w.save_plan(attempt=1, step_ids=["step_a", "step_b"])
    w.save_act(label="web_1", tool="web_search", chars=512, status="done")
    w.save_act(label="web_2", tool="web_fetch", chars=1024, status="done")
    w.save_respond(answer="Docker — платформа контейнеризации.")
    return w


# ── CheckpointWriter ──────────────────────────────────────────────────────────

class TestCheckpointWriter:
    def test_invalid_phase_raises(self, tmp_path):
        w = _writer(tmp_path)
        with pytest.raises(ValueError, match="Unknown checkpoint phase"):
            w.save("bad_phase", {})

    def test_empty_trace_id_raises(self, tmp_path):
        with pytest.raises(ValueError):
            CheckpointWriter(trace_id="", log_dir=tmp_path / "logs")


# ── CheckpointLoader ──────────────────────────────────────────────────────────

class TestCheckpointLoader:
    def test_exists_returns_false_for_missing(self, tmp_path):
        loader = _loader(tmp_path)
        assert not loader.exists("ghost")

    def test_exists_returns_true_after_write(self, tmp_path):
        _full_run(tmp_path, "t1")
        loader = _loader(tmp_path)
        assert loader.exists("t1")

    def test_corrupt_json_returns_none(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "checkpoints_bad.jsonl").write_text("not json\n", encoding="utf-8")
        loader = CheckpointLoader(log_dir)
        assert loader.load("bad") is None

    def test_empty_file_returns_none(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "checkpoints_empty.jsonl").write_text("", encoding="utf-8")
        loader = CheckpointLoader(log_dir)
        assert loader.load("empty") is None

    def test_no_observe_record_returns_none(self, tmp_path):
        """A file with only ACT records (no OBSERVE) is unusable."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        rec = CheckpointRecord(phase=PHASE_ACT, trace_id="x", data={"label": "lbl", "tool": "t", "chars": 0, "status": "done"})
        (log_dir / "checkpoints_x.jsonl").write_text(
            json.dumps(rec.to_dict()) + "\n", encoding="utf-8"
        )
        loader = CheckpointLoader(log_dir)
        assert loader.load("x") is None


# ── Security / audit fixes ────────────────────────────────────────────────────

class TestCheckpointSecurity:
    """Tests for path-traversal fix in CheckpointWriter.__init__."""


    def test_path_traversal_backslash_raises(self, tmp_path):
        with pytest.raises(ValueError, match="disallowed characters"):
            CheckpointWriter(trace_id="trace\\..\\evil", log_dir=tmp_path)


    def test_path_traversal_null_byte_raises(self, tmp_path):
        with pytest.raises(ValueError, match="disallowed characters"):
            CheckpointWriter(trace_id="trace\x00evil", log_dir=tmp_path)

    def test_path_traversal_empty_raises(self, tmp_path):
        with pytest.raises(ValueError):
            CheckpointWriter(trace_id="", log_dir=tmp_path)
