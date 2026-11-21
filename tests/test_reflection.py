"""Tests for core/reflection.py — ReflectionEngine."""

from __future__ import annotations

import json
from pathlib import Path

from core.persistent_memory import PersistentMemoryStore
from core.reflection import (
    ReflectionEngine,
)
from tests.conftest import FakeLLM

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_engine(
    tmp_path: Path,
    *,
    responses: list[str] | None = None,
    log_dir: Path | None = None,
) -> ReflectionEngine:
    memory = PersistentMemoryStore(tmp_path / "data" / "persistent_memory.jsonl")
    llm = FakeLLM(responses=responses or [])
    return ReflectionEngine(
        workspace=tmp_path,
        persistent_memory=memory,
        llm=llm,
        log_dir=log_dir or (tmp_path / "logs"),
    )


def _write_log(log_dir: Path, name: str, events: list[dict]) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    fpath = log_dir / f"{name}.jsonl"
    lines = [json.dumps(ev) for ev in events]
    fpath.write_text("\n".join(lines), encoding="utf-8")
    return fpath


def _tool_call_event(tc_id: str, tool_name: str, trace_id: str = "run_abc") -> dict:
    return {
        "trace_id": trace_id,
        "event": "tool_call",
        "payload": {"id": tc_id, "tool_name": tool_name},
    }


def _tool_error_event(tc_id: str, error: str, trace_id: str = "run_abc") -> dict:
    return {
        "trace_id": trace_id,
        "event": "tool_result",
        "payload": {"tool_call_id": tc_id, "status": "error", "error": error},
    }


def _replan_event(reason: str = "all steps failed", trace_id: str = "run_abc") -> dict:
    return {
        "trace_id": trace_id,
        "event": "replan",
        "payload": {"reason": reason},
    }


def _replan_exhausted_event(trace_id: str = "run_abc") -> dict:
    return {
        "trace_id": trace_id,
        "event": "replan_exhausted",
        "payload": {"max_attempts": 3},
    }


def _lessons_json(*lessons: dict) -> str:
    return json.dumps(list(lessons))


# ── 1. No log directory → empty report ───────────────────────────────────────

def test_reflection_no_log_dir_returns_empty_report(tmp_path: Path):
    engine = _make_engine(tmp_path, log_dir=tmp_path / "nonexistent_logs")
    report = engine.reflect()

    assert report.logs_scanned == 0
    assert report.events_scanned == 0
    assert report.patterns_found == []
    assert report.lessons == []
    assert report.learning_plan is None
    assert report.memory_records_saved == 0
    assert any("log_dir not found" in w for w in report.warnings)


# ── 9. LLM returns non-array JSON → warning ───────────────────────────────────

def test_reflection_handles_llm_non_array(tmp_path: Path):
    log_dir = tmp_path / "logs"
    events = [_replan_event("err", "r1"), _replan_event("err", "r2")]
    _write_log(log_dir, "run", events)
    engine = _make_engine(tmp_path, log_dir=log_dir, responses=['{"oops": true}'])

    report = engine.reflect()

    assert report.lessons == []
    assert any("non-array" in w for w in report.warnings)


# ── 11. action="learn_more" → LearningPlan is generated ─────────────────────

def test_reflection_builds_learning_plan_for_learn_more(tmp_path: Path):
    log_dir = tmp_path / "logs"
    # Need actual files for LearningPlanner to find
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "memory.py").write_text("# memory\n", encoding="utf-8")

    events = [_replan_event("e", "r1"), _replan_event("e", "r2")]
    _write_log(log_dir, "run", events)

    lesson_payload = _lessons_json(
        {
            "insight": "memory handling appears fragile under concurrent access",
            "action": "learn_more",
            "focus_area": "core/memory.py",
            "confidence": 0.8,
        }
    )
    engine = _make_engine(tmp_path, log_dir=log_dir, responses=[lesson_payload])

    report = engine.reflect()

    assert report.learning_plan is not None
    assert "reflection" in report.learning_plan.goal.lower()


# ── 13. Corrupted JSONL lines are skipped silently ───────────────────────────

def test_reflection_skips_corrupted_jsonl_lines(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    fpath = log_dir / "run_corrupt.jsonl"
    fpath.write_text(
        "NOT JSON\n"
        + json.dumps(_replan_event("e", "r1")) + "\n"
        + "{broken\n"
        + json.dumps(_replan_event("e", "r2")) + "\n",
        encoding="utf-8",
    )
    engine = _make_engine(tmp_path, log_dir=log_dir, responses=["[]"])

    report = engine.reflect()

    # Two valid replan events → one pattern
    assert any(p.event_type == "replan" for p in report.patterns_found)
    assert not report.warnings  # corrupted lines are silent


# ── 14. user_summary format ───────────────────────────────────────────────────

def test_reflection_user_summary_contains_key_fields(tmp_path: Path):
    log_dir = tmp_path / "logs"
    events = [_replan_event("e", "r1"), _replan_event("e", "r2")]
    _write_log(log_dir, "run", events)

    lesson_payload = _lessons_json(
        {"insight": "replanning too often", "action": "repair", "focus_area": "core/planner.py", "confidence": 0.7}
    )
    engine = _make_engine(tmp_path, log_dir=log_dir, responses=[lesson_payload])

    report = engine.reflect()
    summary = report.user_summary()

    assert "reflection" in summary
    assert "lessons=" in summary
    assert "repair" in summary


# ── 15. to_dict includes all keys ─────────────────────────────────────────────

def test_reflection_report_to_dict_structure(tmp_path: Path):
    """to_dict carries the report's real counts and lessons.

    Why: the runtime summary reads `lessons_count` from this dict.
    """
    log_dir = tmp_path / "logs"
    events = [
        _tool_call_event("tc1", "web_fetch", "run_a"),
        _tool_error_event("tc1", "timeout", "run_a"),
        _tool_call_event("tc2", "web_fetch", "run_b"),
        _tool_error_event("tc2", "timeout", "run_b"),
    ]
    _write_log(log_dir, "runs", events)
    lesson_payload = _lessons_json(
        {"insight": "web_fetch times out", "action": "repair",
         "focus_area": "tools/web_fetch.py", "confidence": 0.9},
        {"insight": "timeouts cluster on one host", "action": "monitor",
         "focus_area": "", "confidence": 0.6},
    )
    engine = _make_engine(tmp_path, log_dir=log_dir, responses=[lesson_payload])
    report = engine.reflect()
    d = report.to_dict()

    assert d["logs_scanned"] == 1
    assert d["events_scanned"] == 4
    assert [p["tool_name"] for p in d["patterns_found"]] == ["web_fetch"]
    assert d["lessons_count"] == len(d["lessons"]) == 2
    assert [lesson["action"] for lesson in d["lessons"]] == ["repair", "monitor"]
    assert d["memory_records_saved"] == report.memory_records_saved > 0
    assert d["warnings"] == report.warnings
