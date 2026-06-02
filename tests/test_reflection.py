"""Tests for core/reflection.py — ReflectionEngine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.reflection import (
    ErrorPattern,
    Lesson,
    ReflectionConfig,
    ReflectionEngine,
    ReflectionReport,
)
from core.persistent_memory import PersistentMemoryStore
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


def _autonomous_failed_event(kind: str = "tests", summary: str = "pytest crashed", trace_id: str = "run_abc") -> dict:
    return {
        "trace_id": trace_id,
        "event": "autonomous_task_result",
        "payload": {"status": "failed", "task": {"kind": kind}, "summary": summary},
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


# ── 2. Logs with only successes → no patterns ────────────────────────────────

def test_reflection_no_patterns_when_all_success(tmp_path: Path):
    log_dir = tmp_path / "logs"
    events = [
        {"trace_id": "run_a", "event": "tool_result",
         "payload": {"tool_call_id": "tc1", "status": "success", "output": "ok"}},
        {"trace_id": "run_a", "event": "session_start", "payload": {}},
    ]
    _write_log(log_dir, "run_a", events)
    engine = _make_engine(tmp_path, log_dir=log_dir)

    report = engine.reflect()

    assert report.patterns_found == []
    assert report.lessons == []
    assert report.memory_records_saved == 0


# ── 3. Repeated tool errors → pattern extracted with correct tool name ────────

def test_reflection_extracts_tool_error_pattern(tmp_path: Path):
    log_dir = tmp_path / "logs"
    events = [
        _tool_call_event("tc1", "web_fetch", "run_a"),
        _tool_error_event("tc1", "timeout after 30s", "run_a"),
        _tool_call_event("tc2", "web_fetch", "run_b"),
        _tool_error_event("tc2", "connection refused", "run_b"),
    ]
    _write_log(log_dir, "run_a", events)
    engine = _make_engine(tmp_path, log_dir=log_dir, responses=["[]"])

    report = engine.reflect()

    assert len(report.patterns_found) == 1
    p = report.patterns_found[0]
    assert p.event_type == "tool_result_error"
    assert p.tool_name == "web_fetch"
    assert p.count == 2


# ── 4. Repeated replans → pattern extracted ───────────────────────────────────

def test_reflection_extracts_replan_pattern(tmp_path: Path):
    log_dir = tmp_path / "logs"
    events = [
        _replan_event("all steps failed", "run_a"),
        _replan_event("all steps failed", "run_b"),
    ]
    _write_log(log_dir, "multi", events)
    engine = _make_engine(tmp_path, log_dir=log_dir, responses=["[]"])

    report = engine.reflect()

    assert any(p.event_type == "replan" for p in report.patterns_found)


# ── 5. Autonomous task failure → pattern extracted ────────────────────────────

def test_reflection_extracts_autonomous_task_failure(tmp_path: Path):
    log_dir = tmp_path / "logs"
    events = [
        _autonomous_failed_event("tests", "pytest crashed", "run_a"),
        _autonomous_failed_event("tests", "pytest crashed", "run_b"),
    ]
    _write_log(log_dir, "multi", events)
    engine = _make_engine(tmp_path, log_dir=log_dir, responses=["[]"])

    report = engine.reflect()

    assert any(
        p.event_type == "autonomous_task_failed" and p.tool_name == "tests"
        for p in report.patterns_found
    )


# ── 6. min_occurrences filter — patterns below threshold are dropped ──────────

def test_reflection_min_occurrences_filter(tmp_path: Path):
    log_dir = tmp_path / "logs"
    # Only 1 occurrence of replan — below default min_occurrences=2
    events = [_replan_event("single failure", "run_x")]
    _write_log(log_dir, "run_x", events)
    engine = _make_engine(tmp_path, log_dir=log_dir)

    report = engine.reflect(ReflectionConfig(min_occurrences=2))

    assert report.patterns_found == []
    assert report.lessons == []


def test_reflection_min_occurrences_one_accepts_single(tmp_path: Path):
    log_dir = tmp_path / "logs"
    events = [_replan_event("single failure", "run_x")]
    _write_log(log_dir, "run_x", events)
    engine = _make_engine(tmp_path, log_dir=log_dir, responses=["[]"])

    report = engine.reflect(ReflectionConfig(min_occurrences=1))

    assert len(report.patterns_found) == 1


# ── 7. LLM returns valid JSON → lessons parsed and saved ─────────────────────

def test_reflection_saves_lessons_to_memory(tmp_path: Path):
    log_dir = tmp_path / "logs"
    events = [
        _tool_call_event("tc1", "web_fetch", "run_a"),
        _tool_error_event("tc1", "timeout", "run_a"),
        _tool_call_event("tc2", "web_fetch", "run_b"),
        _tool_error_event("tc2", "timeout", "run_b"),
    ]
    _write_log(log_dir, "runs", events)

    lesson_payload = _lessons_json(
        {
            "insight": "web_fetch times out frequently; retry or increase timeout",
            "action": "repair",
            "focus_area": "tools/web_fetch.py",
            "confidence": 0.9,
        }
    )
    engine = _make_engine(tmp_path, log_dir=log_dir, responses=[lesson_payload])
    memory = engine.persistent_memory

    report = engine.reflect()

    assert len(report.lessons) == 1
    assert report.lessons[0].action == "repair"
    assert report.lessons[0].focus_area == "tools/web_fetch.py"
    assert report.memory_records_saved == 1

    saved = memory.load()
    assert len(saved) == 1
    assert saved[0].type == "episodic"
    assert "reflection" in saved[0].tags
    assert "repair" in saved[0].tags


# ── 8. LLM returns garbage → warning, no lessons, no crash ───────────────────

def test_reflection_handles_llm_json_failure(tmp_path: Path):
    log_dir = tmp_path / "logs"
    events = [_replan_event("err", "r1"), _replan_event("err", "r2")]
    _write_log(log_dir, "run", events)
    engine = _make_engine(tmp_path, log_dir=log_dir, responses=["NOT VALID JSON {{{"])

    report = engine.reflect()

    assert report.lessons == []
    assert report.memory_records_saved == 0
    assert any("JSON parse failed" in w for w in report.warnings)


# ── 9. LLM returns non-array JSON → warning ───────────────────────────────────

def test_reflection_handles_llm_non_array(tmp_path: Path):
    log_dir = tmp_path / "logs"
    events = [_replan_event("err", "r1"), _replan_event("err", "r2")]
    _write_log(log_dir, "run", events)
    engine = _make_engine(tmp_path, log_dir=log_dir, responses=['{"oops": true}'])

    report = engine.reflect()

    assert report.lessons == []
    assert any("non-array" in w for w in report.warnings)


# ── 10. max_lessons caps the output ──────────────────────────────────────────

def test_reflection_config_max_lessons(tmp_path: Path):
    log_dir = tmp_path / "logs"
    events = [_replan_event("e", "r1"), _replan_event("e", "r2")]
    _write_log(log_dir, "run", events)

    many_lessons = _lessons_json(
        *[
            {"insight": f"lesson {i}", "action": "monitor", "focus_area": "", "confidence": 0.5}
            for i in range(8)
        ]
    )
    engine = _make_engine(tmp_path, log_dir=log_dir, responses=[many_lessons])

    report = engine.reflect(ReflectionConfig(max_lessons=3))

    assert len(report.lessons) == 3


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


# ── 12. action="monitor" only → no LearningPlan ──────────────────────────────

def test_reflection_no_learning_plan_for_monitor_only(tmp_path: Path):
    log_dir = tmp_path / "logs"
    events = [_replan_event("e", "r1"), _replan_event("e", "r2")]
    _write_log(log_dir, "run", events)

    lesson_payload = _lessons_json(
        {
            "insight": "minor flakiness; keep watching",
            "action": "monitor",
            "focus_area": "",
            "confidence": 0.4,
        }
    )
    engine = _make_engine(tmp_path, log_dir=log_dir, responses=[lesson_payload])

    report = engine.reflect()

    assert report.learning_plan is None


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
    engine = _make_engine(tmp_path, log_dir=tmp_path / "no_logs")
    report = engine.reflect()
    d = report.to_dict()

    for key in (
        "logs_scanned", "events_scanned", "patterns_found",
        "lessons_count", "lessons", "learning_plan",
        "memory_records_saved", "warnings",
    ):
        assert key in d, f"missing key: {key}"
