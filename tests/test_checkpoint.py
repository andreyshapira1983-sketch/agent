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
import tempfile
from pathlib import Path

import pytest

from core.checkpoint import (
    PHASE_ACT,
    PHASE_OBSERVE,
    PHASE_PLAN,
    PHASE_RESPOND,
    CheckpointLoader,
    CheckpointRecord,
    CheckpointWriter,
    ResumeContext,
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
    def test_creates_file_on_first_write(self, tmp_path):
        w = _writer(tmp_path)
        assert not w.path.exists()
        w.save_observe(question="hello")
        assert w.path.exists()

    def test_path_contains_trace_id(self, tmp_path):
        w = _writer(tmp_path, trace_id="abc-123")
        w.save_observe(question="x")
        assert "abc-123" in w.path.name

    def test_file_has_valid_jsonl(self, tmp_path):
        w = _full_run(tmp_path)
        lines = [
            json.loads(ln)
            for ln in w.path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        assert len(lines) == 5  # observe + plan + act + act + respond

    def test_each_line_has_required_fields(self, tmp_path):
        w = _writer(tmp_path)
        w.save_observe(question="test")
        line = json.loads(w.path.read_text(encoding="utf-8").strip())
        for field in ("checkpoint_id", "phase", "trace_id", "ts", "data"):
            assert field in line, f"missing field: {field}"

    def test_phase_observe_stored(self, tmp_path):
        w = _writer(tmp_path)
        rec = w.save_observe(question="my question", file_hint="file.py")
        assert rec.phase == PHASE_OBSERVE
        assert rec.data["question"] == "my question"
        assert rec.data["file_hint"] == "file.py"

    def test_phase_plan_stored(self, tmp_path):
        w = _writer(tmp_path)
        w.save_observe(question="x")
        rec = w.save_plan(attempt=2, step_ids=["s1", "s2"])
        assert rec.phase == PHASE_PLAN
        assert rec.data["attempt"] == 2
        assert rec.data["step_ids"] == ["s1", "s2"]

    def test_phase_act_stored(self, tmp_path):
        w = _writer(tmp_path)
        w.save_observe(question="x")
        rec = w.save_act(label="lbl", tool="file_read", chars=200, status="done")
        assert rec.phase == PHASE_ACT
        assert rec.data["label"] == "lbl"
        assert rec.data["tool"] == "file_read"
        assert rec.data["status"] == "done"

    def test_phase_respond_stored(self, tmp_path):
        w = _writer(tmp_path)
        w.save_observe(question="x")
        rec = w.save_respond(answer="result text")
        assert rec.phase == PHASE_RESPOND
        assert rec.data["answer"] == "result text"
        assert rec.data["chars"] == len("result text")

    def test_invalid_phase_raises(self, tmp_path):
        w = _writer(tmp_path)
        with pytest.raises(ValueError, match="Unknown checkpoint phase"):
            w.save("bad_phase", {})

    def test_empty_trace_id_raises(self, tmp_path):
        with pytest.raises(ValueError):
            CheckpointWriter(trace_id="", log_dir=tmp_path / "logs")

    def test_append_only(self, tmp_path):
        """Multiple writes accumulate; file is not truncated."""
        w = _writer(tmp_path)
        w.save_observe(question="first")
        w.save_observe(question="second")
        lines = w.path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2


# ── CheckpointLoader ──────────────────────────────────────────────────────────

class TestCheckpointLoader:
    def test_missing_file_returns_none(self, tmp_path):
        loader = _loader(tmp_path)
        assert loader.load("nonexistent-trace") is None

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


# ── Round-trip ────────────────────────────────────────────────────────────────

class TestRoundTrip:
    def test_full_run_returns_resume_context(self, tmp_path):
        _full_run(tmp_path, "rt1")
        ctx = _loader(tmp_path).load("rt1")
        assert isinstance(ctx, ResumeContext)

    def test_question_preserved(self, tmp_path):
        _full_run(tmp_path, "rt2")
        ctx = _loader(tmp_path).load("rt2")
        assert ctx.question == "что такое Docker?"

    def test_answer_preserved(self, tmp_path):
        _full_run(tmp_path, "rt3")
        ctx = _loader(tmp_path).load("rt3")
        assert ctx.answer == "Docker — платформа контейнеризации."

    def test_artifacts_reconstructed(self, tmp_path):
        _full_run(tmp_path, "rt4")
        ctx = _loader(tmp_path).load("rt4")
        assert "web_1" in ctx.artifacts
        assert ctx.artifacts["web_1"]["tool"] == "web_search"
        assert "web_2" in ctx.artifacts

    def test_last_phase_is_respond(self, tmp_path):
        _full_run(tmp_path, "rt5")
        ctx = _loader(tmp_path).load("rt5")
        assert ctx.last_phase == PHASE_RESPOND

    def test_file_hint_none_preserved(self, tmp_path):
        _full_run(tmp_path, "rt6")
        ctx = _loader(tmp_path).load("rt6")
        assert ctx.file_hint is None

    def test_file_hint_value_preserved(self, tmp_path):
        w = _writer(tmp_path, "rt7")
        w.save_observe(question="q", file_hint="core/loop.py")
        w.save_respond(answer="ans")
        ctx = _loader(tmp_path).load("rt7")
        assert ctx.file_hint == "core/loop.py"

    def test_attempt_number_from_plan(self, tmp_path):
        w = _writer(tmp_path, "rt8")
        w.save_observe(question="q")
        w.save_plan(attempt=1, step_ids=[])
        w.save_plan(attempt=2, step_ids=["s1"])  # replan
        w.save_respond(answer="ans")
        ctx = _loader(tmp_path).load("rt8")
        assert ctx.attempt == 2  # last plan attempt wins


# ── Partial run ───────────────────────────────────────────────────────────────

class TestPartialRun:
    def test_crash_before_respond_returns_context_with_no_answer(self, tmp_path):
        """Crash after ACT but before RESPOND → answer=None (safe: re-run)."""
        w = _writer(tmp_path, "partial")
        w.save_observe(question="crash test")
        w.save_plan(attempt=1, step_ids=["step_x"])
        w.save_act(label="file_1", tool="file_read", chars=100, status="done")
        ctx = _loader(tmp_path).load("partial")
        assert ctx is not None
        assert ctx.answer is None
        assert ctx.last_phase == PHASE_ACT

    def test_only_observe_written(self, tmp_path):
        """Only OBSERVE (crash immediately after) → context with no answer."""
        w = _writer(tmp_path, "early")
        w.save_observe(question="early crash")
        ctx = _loader(tmp_path).load("early")
        assert ctx is not None
        assert ctx.answer is None
        assert ctx.last_phase == PHASE_OBSERVE

    def test_failed_act_not_in_artifacts(self, tmp_path):
        """ACT records with status!='done' must NOT appear in artifacts."""
        w = _writer(tmp_path, "fail_act")
        w.save_observe(question="test")
        w.save(PHASE_ACT, {"label": "bad_lbl", "tool": "web_search", "chars": 0, "status": "failed"})
        w.save_respond(answer="fallback answer")
        ctx = _loader(tmp_path).load("fail_act")
        assert "bad_lbl" not in ctx.artifacts
