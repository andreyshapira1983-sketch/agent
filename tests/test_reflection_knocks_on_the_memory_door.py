"""Reflection writes through the same door as every other agent-auto write
(audit M7, block 4, 2026-09-03).

Measured on the live store: 837 persistent records, 635 owned by
`reflection_engine`, 625 written on one day — `_save_lessons` called
`save_many` directly and no write policy ever saw them. Now each lesson is an
observation of one day of logs, tagged as such, and `MemoryWritePolicy`
decides: consent, secrets, near-duplicates. A refusal is logged by reason.
"""
from __future__ import annotations

from pathlib import Path

from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.reflection import ReflectionEngine
from tests.test_reflection import (
    FakeLLM,
    _lessons_json,
    _tool_call_event,
    _tool_error_event,
    _write_log,
)


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log(self, event, payload=None, **_kw) -> None:
        self.events.append((event, dict(payload or {})))


def _engine(tmp_path: Path, *lessons: dict, memory: PersistentMemoryStore | None = None):
    log_dir = tmp_path / "logs"
    _write_log(log_dir, "runs", [
        _tool_call_event("tc1", "web_fetch", "run_a"),
        _tool_error_event("tc1", "timeout", "run_a"),
        _tool_call_event("tc2", "web_fetch", "run_b"),
        _tool_error_event("tc2", "timeout", "run_b"),
    ])
    memory = memory or PersistentMemoryStore(tmp_path / "data" / "persistent_memory.jsonl")
    logger = _Log()
    engine = ReflectionEngine(
        workspace=tmp_path, persistent_memory=memory,
        llm=FakeLLM(responses=[_lessons_json(*lessons)]),
        log_dir=log_dir, logger=logger,
    )
    engine.events = logger.events  # type: ignore[attr-defined]
    return engine, memory


_CLEAN = {
    "insight": "web_fetch times out on slow hosts; retry once before giving up",
    "action": "repair", "focus_area": "tools/web_fetch.py", "confidence": 0.9,
}


def test_a_clean_lesson_is_saved_with_its_kind_and_source(tmp_path: Path) -> None:
    engine, memory = _engine(tmp_path, _CLEAN)

    report = engine.reflect()

    assert report.memory_records_saved == 1
    saved = memory.load()[0]
    assert "[НАБЛЮДЕНИЕ, один день]" in saved.tags and "insight" in saved.tags
    assert saved.source == "agent-auto"
    assert saved.type == "episodic" and "reflection" in saved.tags


def test_the_same_insight_twice_is_saved_once(tmp_path: Path) -> None:
    """The one-day flood: 625 records, most restating each other."""
    engine, memory = _engine(tmp_path, _CLEAN, dict(_CLEAN))

    report = engine.reflect()

    assert report.memory_records_saved == 1
    assert len(memory.load()) == 1
    refused = [p for e, p in engine.events if e == "reflection_lesson_refused"]
    assert refused and any("duplicate" in r.lower() for r in refused[0]["reasons"]), refused


def test_an_existing_record_blocks_its_echo(tmp_path: Path) -> None:
    memory = PersistentMemoryStore(tmp_path / "data" / "persistent_memory.jsonl")
    memory.save(MemoryRecord(type="episodic", content=_CLEAN["insight"],
                             tags=["insight"], owner="self"))
    engine, memory = _engine(tmp_path, _CLEAN, memory=memory)

    report = engine.reflect()

    assert report.memory_records_saved == 0
    assert len(memory.load()) == 1


def test_a_lesson_bearing_a_secret_never_reaches_memory(tmp_path: Path) -> None:
    leaky = dict(_CLEAN, insight=(
        "web_fetch fails unless OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456 is set"
    ))
    engine, memory = _engine(tmp_path, leaky)

    report = engine.reflect()

    assert report.memory_records_saved == 0
    assert memory.load() == []
    assert any(e == "reflection_lesson_refused" for e, _p in engine.events)
