"""Средние дефекты памяти (проверка 17.11, средняя): слияние, чтение, чистка, потолок."""
from __future__ import annotations

import datetime as dt
import itertools
import json
import threading
import time

import tools.memory_bank as mb
from core.memory_consolidation import consolidate
from core.memory_hygiene import deduplicate_memory
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from tools.memory_recall import MemoryRecallTool


class _LLM:
    def __init__(self, answer: str):
        self.answer = answer

    def complete(self, system, user, max_tokens=0, temperature=0):
        return self.answer


def _conclusion() -> MemoryRecord:
    return MemoryRecord(content="Вопрос: x\nВывод: y 12", tags=["conclusion"], type="semantic")


def test_consolidate_dict_target_falls_back_to_add():
    r = _conclusion()
    llm = _LLM(json.dumps({"operation": "DELETE", "target": {"id": r.id}, "merged": None}))
    assert consolidate(llm, "c", "x", [r]).operation == "ADD"


def _store(tmp_path) -> PersistentMemoryStore:
    return PersistentMemoryStore(tmp_path / "pm.jsonl")


def _stale_trio():
    now = dt.datetime.now(dt.timezone.utc)
    r0 = MemoryRecord(content="deploy goes to staging first then prod", tags=["fact"],
                      type="semantic", created_at=now - dt.timedelta(days=3))
    r2 = MemoryRecord(content="deploy goes to staging first then prod.", tags=["fact"],
                      type="semantic", created_at=now - dt.timedelta(days=1))
    # r1 is the newest of the stale ones by time but is superseded by r2.
    r1 = MemoryRecord(content="deploy goes straight to prod", tags=["fact"], type="semantic",
                      superseded_by=r2.id, created_at=now - dt.timedelta(hours=1))
    return r0, r1, r2


def test_recall_marks_superseded_and_ranks_current_first(tmp_path):
    r0, r1, r2 = _stale_trio()
    s = _store(tmp_path)
    s.save_many([r0, r1, r2])
    recs = MemoryRecallTool(store=s).run(term="deploy")["records"]
    assert [x["id"] for x in recs][-1] == r1.id          # stale one last
    stale = recs[-1]
    assert stale["superseded_by"] == r2.id
    assert "замещено" in stale["trust"] and r2.id in stale["trust"]
    for cur in recs[:-1]:
        assert cur["superseded_by"] is None
        assert "замещено" not in cur["trust"]


def test_recall_newest_first_across_time_zones(tmp_path):
    tz3 = dt.timezone(dt.timedelta(hours=3))
    older = MemoryRecord(content="port is 8080 (older)", tags=["fact"], type="semantic",
                         created_at=dt.datetime(2026, 11, 16, 14, 0, tzinfo=tz3))  # 11:00 UTC
    newer = MemoryRecord(content="port is 9090 (newer)", tags=["fact"], type="semantic",
                         created_at=dt.datetime(2026, 11, 16, 12, 0, tzinfo=dt.timezone.utc))
    s = _store(tmp_path)
    s.save_many([older, newer])
    recs = MemoryRecallTool(store=s).run(term="port")["records"]
    assert [x["id"] for x in recs] == [newer.id, older.id]


def test_dedup_two_targets_that_match_both_stay(tmp_path):
    now = dt.datetime.now(dt.timezone.utc)
    a = MemoryRecord(content="the cache lives in redis", tags=["fact"], type="semantic",
                     created_at=now - dt.timedelta(days=2))
    b = MemoryRecord(content="the cache lives in redis.", tags=["fact"], type="semantic",
                     created_at=now - dt.timedelta(days=1))
    old_a = MemoryRecord(content="x one", superseded_by=a.id, created_at=now - dt.timedelta(days=5))
    old_b = MemoryRecord(content="y two", superseded_by=b.id, created_at=now - dt.timedelta(days=5))
    s = _store(tmp_path)
    s.save_many([a, b, old_a, old_b])
    rep = deduplicate_memory(s)
    assert a.id not in rep.deleted and b.id not in rep.deleted


def _run_parallel(tool, n=30):
    res: list[dict] = []
    lock = threading.Lock()

    def one():
        r = tool.run(text="t", kind="k", provenance="p")
        with lock:
            res.append(r)

    ts = [threading.Thread(target=one) for _ in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return res


def test_bank_ceiling_holds_under_parallel_calls(monkeypatch):
    ctr = itertools.count()

    def slow_door(store, policy, text, kind, provenance):
        time.sleep(0.05)
        return f"mem_{next(ctr)}", ""

    monkeypatch.setattr(mb, "memory_door_verdict", slow_door)
    mb.reset_process_ceiling()
    try:
        tool = mb.MemoryBankTool(store=None, policy=None)
        res = _run_parallel(tool)
        assert sum(r["banked"] for r in res) == tool.max_writes_per_process
    finally:
        mb.reset_process_ceiling()


def test_bank_own_counter_ceiling_holds_under_parallel_calls(monkeypatch):
    def slow_door(store, policy, text, kind, provenance):
        time.sleep(0.05)
        return "mem_x", ""

    monkeypatch.setattr(mb, "memory_door_verdict", slow_door)
    tool = mb.MemoryBankTool(store=None, policy=None, max_writes_per_process=3)
    assert sum(r["banked"] for r in _run_parallel(tool, 15)) == 3


def test_bank_refused_write_releases_its_slot(monkeypatch):
    answers = iter([(None, "duplicate")] + [("mem_ok", "")] * 5)
    monkeypatch.setattr(mb, "memory_door_verdict", lambda *a: next(answers))
    tool = mb.MemoryBankTool(store=None, policy=None, max_writes_per_process=2)
    out = [tool.run(text="t", kind="k", provenance="p") for _ in range(4)]
    assert [r["banked"] for r in out] == [False, True, True, False]
    assert out[-1]["refused_by"].startswith("ceiling")


def test_bank_door_exception_releases_its_slot(monkeypatch):
    calls = {"n": 0}

    def door(*a):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk")
        return "mem_ok", ""

    monkeypatch.setattr(mb, "memory_door_verdict", door)
    tool = mb.MemoryBankTool(store=None, policy=None, max_writes_per_process=1)
    try:
        tool.run(text="t", kind="k", provenance="p")
    except RuntimeError:
        pass
    assert tool.run(text="t", kind="k", provenance="p")["banked"] is True
