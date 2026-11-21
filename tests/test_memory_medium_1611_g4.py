"""Средние дефекты памяти уроков, области субагента и шаблонов (проверка 16.11, средняя)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from core import failure_cards as fc
from core.failure_cards import Card, CardStore, learn_from_events, note_for
from core.self_build_memory import record_self_build_episode
from core.smart_memory import EpisodicMemoryStore
from core.subagent_memory_scope import propose_subagent
from core.work_kinds import work_kinds
from core.workflow_memory import WorkflowMemoryStore

# ── 1. failure_cards: битые строки пропускаются ────────────────────────────

@pytest.mark.parametrize("body", [
    '"signature"\n',
    '{"_digested": {"t": "x"}}\n',
    '{"_digested": [1]}\n',
    b"\xff\xfe{}\n",
    '{"sig": 5, "tool": "t", "error": "e"}\n',
    '{"sig":"python_probe|E","tool":"python_probe","error":"E","seen":"many","status":"active","lesson":"L"}\n',
])
def test_corrupt_card_rows_are_skipped_not_fatal(tmp_path: Path, body: str | bytes) -> None:
    p = tmp_path / "data" / "failure_cards.jsonl"
    p.parent.mkdir()
    good = json.dumps({"sig": "python_probe|E", "tool": "python_probe", "error": "E",
                       "status": "active", "lesson": "Когда E — делай Y"}) + "\n"
    raw = body if isinstance(body, bytes) else body.encode("utf-8")
    p.write_bytes(raw + good.encode("utf-8"))
    store = CardStore(tmp_path)
    assert "python_probe|E" in store.cards
    assert isinstance(store.cards["python_probe|E"].seen, int)
    assert note_for(tmp_path, "python_probe", "E") is not None


# ── 3. два писателя не теряют карточки ─────────────────────────────────────

def test_two_writers_do_not_lose_a_card(tmp_path: Path) -> None:
    a, b = CardStore(tmp_path), CardStore(tmp_path)
    a.cards["x|A"] = Card(sig="x|A", tool="x", error="A", status="active", lesson="lesson A")
    a.digested["t1"] = 4
    a.save()
    b.cards["y|B"] = Card(sig="y|B", tool="y", error="B")
    b.save()
    on_disk = CardStore(tmp_path)
    assert {"x|A", "y|B"} <= set(on_disk.cards)
    assert on_disk.digested.get("t1") == 4


def test_learning_holds_the_cards_lock_and_tmp_is_unique(tmp_path: Path) -> None:
    held: list[bool] = []
    path = tmp_path / fc.CARDS_RELPATH

    class Llm:
        def complete(self, **kw):
            held.append(fc._lock_held(path))
            return "НЕТ"

    def ev(i, res):
        ts = f"2026-10-01T00:00:{i:02d}"
        return [{"event": "tool_call", "ts": ts, "payload": {"id": f"c{i}", "tool_name": "web_fetch",
                                                            "arguments": {"url": str(i)}}},
                {"event": "tool_result", "ts": ts, "payload": {"tool_call_id": f"c{i}", **res}}]
    events = ev(1, {"status": "error", "error": "HTTPError: 404 Not Found"}) + \
        ev(2, {"status": "success", "output": {"text": "ok"}})
    learn_from_events(tmp_path, events, Llm(), trace_id="t")
    # Since the low-severity fix the model is asked WITHOUT the lock (a slow
    # call must not starve another process); lost-update safety is kept by
    # re-reading under the lock before saving.
    assert held == [False]
    assert not fc._lock_held(path)
    assert fc._tmp_name(path) != fc._tmp_name(path)


# ── 4. область субагента держит код, а не модель ───────────────────────────

class _Llm:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, **kw):
        return self.reply if self.reply is None else json.dumps(self.reply)


def test_none_model_reply_is_llm_error() -> None:
    r = propose_subagent("x", llm=_Llm(None))
    assert r.status == "llm_error"


# ── 6. отказ ворот — не урок и не повторяется ──────────────────────────────


def test_identical_gate_refusals_are_banked_once(tmp_path: Path) -> None:
    store = EpisodicMemoryStore(tmp_path / "episodic_memory.jsonl")
    agent = NS(episodic_store=store)
    res = {"status": "denied_cooldown", "reason": "denied recently", "target_path": "core/a.py"}
    ok = [record_self_build_episode(agent, kind="self-build-produce", result=res) for _ in range(5)]
    assert ok.count(True) == 1
    assert sum("lesson" in e.tags for e in store.load()) == 0


# ── 7. шаблоны: битая строка пропускается, строка-шаги — один шаг ──────────

def _kind() -> str:
    return next(iter(work_kinds("исправь ошибку в тесте")))


def test_bad_workflow_row_is_skipped(tmp_path: Path) -> None:
    p = tmp_path / "workflow_memory.jsonl"
    k = _kind()
    rows = [{"kind": k, "description": "bad", "steps": ["a", "b"], "source_count": "n/a"},
            {"kind": k, "description": "bad2", "steps": 5},
            {"kind": k, "description": "good", "steps": ["a", "b"], "source_count": 2}]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    got = WorkflowMemoryStore(p).for_question("исправь ошибку в тесте")
    assert [w.description for w in got] == ["good"]


def test_string_steps_are_one_step(tmp_path: Path) -> None:
    p = tmp_path / "workflow_memory.jsonl"
    p.write_text(json.dumps({"kind": _kind(), "description": "d", "steps": "read then write"}) + "\n",
                 encoding="utf-8")
    assert WorkflowMemoryStore(p).load()[0].steps == ("read then write",)
