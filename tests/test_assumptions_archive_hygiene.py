"""MIR-027, открытая половина — архив посылок ограничен, не вечен.

Правило оператора («сохранить — не значит постоянно помнить») сняло
автоподмешивание; остался архив, который рос без прунинга и с дублями
(профиль Layer 4 переизвлекает тот же текст — измерено вживую: языковая
посылка сохранена дважды за два хода). Компакция встаёт в существующий
ряд гигиены (`run_maintenance_pass`): дубль (category, text) схлопывается
в НОВЕЙШУЮ строку, хвост ограничен капом, dry-run считает и не трогает.
Механизм «архивное → релевантность → применимость → активация» остаётся
контракту памяти — здесь только границы спящего.
"""
from __future__ import annotations

from pathlib import Path

from core.assumption_registry import Assumption, AssumptionStore


def _store_with(tmp_path: Path, rows: list[tuple[str, str, str]]) -> AssumptionStore:
    store = AssumptionStore(tmp_path / "assumptions.jsonl")
    for run_id, category, text in rows:
        store.save(Assumption(run_id=run_id, category=category, text=text))
    return store


def test_duplicates_collapse_to_the_newest_row(tmp_path):
    store = _store_with(tmp_path, [
        ("r1", "language", "The user expects a Russian-language response."),
        ("r1", "scope", "The referenced file is located inside the workspace root."),
        ("r2", "language", "The user expects a Russian-language response."),
    ])
    report = store.compact()
    assert report["scanned"] == 3
    assert report["duplicates_removed"] == 1
    assert report["kept"] == 2
    rows = store.load_recent(10)
    langs = [a for a in rows if a.category == "language"]
    assert len(langs) == 1
    assert langs[0].run_id == "r2", "the NEWEST duplicate must win"


def test_the_cap_bounds_the_dormant_archive(tmp_path):
    store = _store_with(tmp_path, [
        ("r", "general", f"assumption number {i} is unique") for i in range(30)
    ])
    report = store.compact(keep_last=10)
    assert report["over_cap_removed"] == 20
    assert report["kept"] == 10
    texts = [a.text for a in store.load_recent(50)]
    assert "assumption number 29 is unique" in texts
    assert "assumption number 0 is unique" not in texts


def test_negative_keep_last_is_refused(tmp_path):
    """Review round #282: a negative cap is a caller error — failing fast
    beats silently emptying the archive."""
    import pytest

    store = _store_with(tmp_path, [("r1", "language", "text")])
    with pytest.raises(ValueError):
        store.compact(keep_last=-1)


def test_rows_without_a_usable_key_are_never_merged(tmp_path):
    """Review round #282: two DISTINCT schema-poor rows (both lacking `text`)
    must not collide on the empty-string fallback key and silently lose one."""
    from core.state_integrity import append_state_jsonl_unlocked

    path = tmp_path / "assumptions.jsonl"
    store = AssumptionStore(path)
    append_state_jsonl_unlocked(path, [
        {"category": "orphan", "note": "первая строка без text"},
        {"category": "orphan", "note": "вторая, другая по содержанию"},
    ])
    report = store.compact()
    assert report["duplicates_removed"] == 0
    assert report["kept"] == 2


def test_empty_category_is_not_an_identity_either(tmp_path):
    """Review round #282, second pass: category="" is as unusable an identity
    as a missing one — two such rows must both survive."""
    from core.state_integrity import append_state_jsonl_unlocked

    path = tmp_path / "assumptions.jsonl"
    store = AssumptionStore(path)
    append_state_jsonl_unlocked(path, [
        {"category": "", "text": "one shared text", "note": "первая"},
        {"category": "", "text": "one shared text", "note": "вторая"},
    ])
    report = store.compact()
    assert report["duplicates_removed"] == 0
    assert report["kept"] == 2


def test_dry_run_counts_without_touching_the_file(tmp_path):
    store = _store_with(tmp_path, [
        ("r1", "language", "same text"),
        ("r2", "language", "same text"),
    ])
    before = (tmp_path / "assumptions.jsonl").read_bytes()
    report = store.compact(dry_run=True)
    assert report["duplicates_removed"] == 1
    assert (tmp_path / "assumptions.jsonl").read_bytes() == before


def test_maintenance_pass_reports_the_compaction(tmp_path):
    """The wiring corner: the hygiene pass carries the new counters and the
    journal gets its own event — governed by the same `hygiene` sink."""
    from core.logger import TraceLogger
    from core.loop import AgentLoop
    from core.memory import WorkingMemory
    from core.planner import LLMPlanner
    from core.policy import PolicyGate
    from tools.base import ToolRegistry

    class _LLM:
        provider = "mock"
        model = "mock-1"

        def complete(self, system, user, **kw):
            return "{}"

    llm = _LLM()
    registry = ToolRegistry()
    logger = TraceLogger(trace_id="trace_hyg", log_dir=tmp_path, verbose=False)
    events: list[tuple[str, dict]] = []
    original = logger.log

    def spy(event, payload=None, **kw):
        events.append((event, payload))
        return original(event, payload, **kw)

    logger.log = spy  # type: ignore[method-assign]
    store = _store_with(tmp_path, [
        ("r1", "language", "same text"),
        ("r2", "language", "same text"),
    ])
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
        assumption_store=store,
    )
    report = agent.run_maintenance_pass(dry_run=False)
    assert report["assumptions_duplicates_removed"] == 1
    assert any(e == "assumptions_archive_compact" for e, _ in events)
    assert len(store.load_recent(10)) == 1
