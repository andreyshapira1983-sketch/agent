"""Мелкие дефекты памяти (проверка 16.11, мелкая, группа a)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import tools.memory_bank as memory_bank_mod
from core.loop_memory_commands import AgentLoopMemoryCommands
from core.loop_memory_read import AgentLoopMemoryRead
from core.memory import WorkingMemory
from core.memory_policy import MemoryWritePolicy
from core.persistent_memory import PersistentMemoryStore
from core.smart_memory import EpisodeRecord, EpisodicMemoryStore
from tools.memory_bank import MemoryBankTool


class _Log:
    def __init__(self):
        self.events = []

    def log(self, name, payload=None, *a, **k):
        self.events.append((name, payload))


def _read_host(store, log=None):
    return SimpleNamespace(
        episodic_store=store, procedural_store=None,
        log=log or _Log(),
        _last_episode_records=[], _last_procedure_records=[],
        _last_best_similar_episode=None, _last_best_similar_score=0.0,
        _family_product_warnings=lambda episodes: [],
        _code_checked=lambda block, source: block,
        _workflow_block=lambda question: "",
        _procedures_unless_workflow=lambda question: ([], {}),
    )


# 1 -------------------------------------------------------------------------
def test_lesson_fallback_filters_by_path_before_the_cap(tmp_path):
    s = EpisodicMemoryStore(tmp_path / "e.jsonl")
    s.save(EpisodeRecord(goal="g", question="x", outcome="failed", tags=("lesson",),
                         summary="core/foo.py: never call bar() twice", usage_eligible=True,
                         id="ep-foo", created_at="2026-10-01T00:00:00+00:00"))
    for i in range(5):
        s.save(EpisodeRecord(goal="g", question="y", outcome="failed", tags=("lesson",),
                             summary=f"other lesson {i}", usage_eligible=True,
                             id=f"ep-o{i}", created_at=f"2026-11-0{i + 1}T00:00:00+00:00"))
    # Основной поиск молчит — проверяется только запасной путь по пути файла.
    s.search_with_report = lambda *a, **k: SimpleNamespace(episodes=[], rejected_by={})
    me = _read_host(s)
    AgentLoopMemoryRead._retrieve_experience_memory(me, ":repair core/foo.py")
    assert "ep-foo" in [ep.id for ep in me._last_episode_records]


# 3 -------------------------------------------------------------------------
class _BrokenRegistry:
    def recent(self):
        return []

    def append(self, event):
        raise OSError("disk full")


def _cmd_host(tmp_path, registry=None):
    return SimpleNamespace(
        log=_Log(),
        persistent_store=PersistentMemoryStore(tmp_path / "pm.jsonl"),
        write_policy=MemoryWritePolicy(),
        memory_write_registry=registry,
    )


def test_echo_registry_append_failure_is_logged(tmp_path):
    me = _cmd_host(tmp_path, registry=_BrokenRegistry())
    decision, record = AgentLoopMemoryCommands.remember(
        me, "The build cache lives under var/cache and is safe to wipe weekly.",
        tags=["insight"], source="agent-auto", owner="self",
    )
    assert decision.decision == "save" and record is not None
    names = [n for n, _ in me.log.events]
    assert "memory_echo_registry_append_failed" in names


_PII = "ping a.b@example.com c.d@example.org +1 555 123 4567 x"


def test_duplicate_check_compares_the_redacted_form(tmp_path):
    me = _cmd_host(tmp_path)
    tags = ["sensitive-data-consent"]
    first, rec = AgentLoopMemoryCommands.remember(me, _PII, tags=tags, source="user-explicit")
    assert first.decision == "save" and rec is not None and "[REDACTED" in rec.content
    second, rec2 = AgentLoopMemoryCommands.remember(me, _PII, tags=tags, source="user-explicit")
    assert second.decision == "reject" and rec2 is None
    assert any("duplicate" in r for r in second.reasons)
    assert len(me.persistent_store.load()) == 1


# 4 -------------------------------------------------------------------------
def test_cache_evicts_least_recently_used():
    wm = WorkingMemory(max_artifacts=2)
    wm.cache_store("read", {"p": "a"}, "old", "a")
    wm.cache_store("read", {"p": "b"}, "b", "b")
    wm.cache_store("read", {"p": "a"}, "fresh", "a")  # обновили a
    wm.cache_store("read", {"p": "c"}, "c", "c")
    assert wm.cache_lookup("read", {"p": "a"})["output"] == "fresh"
    assert wm.cache_lookup("read", {"p": "b"}) is None


# 7 -------------------------------------------------------------------------
_KIND = "[ВЫВОД, проверен боем]"


@pytest.fixture
def fresh_process_ceiling():
    memory_bank_mod.reset_process_ceiling()
    yield
    memory_bank_mod.reset_process_ceiling()


def test_memory_bank_ceiling_is_per_process(tmp_path, monkeypatch, fresh_process_ceiling):
    monkeypatch.setattr(MemoryBankTool, "max_writes_per_process", 2)
    texts = [
        "Первый вывод: страж повторов судит предмет работы, а не формулировку цели.",
        "Второй вывод: ожидание внутри смены — исход попытки, а не конец рабочего дня.",
        "Третий вывод: улика без происхождения не становится памятью ни при каком слове.",
    ]
    outs = []
    for i, t in enumerate(texts):  # новый экземпляр на каждый вызов — как пересборка реестра
        tool = MemoryBankTool(store=PersistentMemoryStore(tmp_path / f"pm{i}.jsonl"),
                              policy=MemoryWritePolicy())
        outs.append(tool.run(text=t, kind=_KIND, provenance=f"test:{i}"))
    assert [o["banked"] for o in outs] == [True, True, False]
    assert outs[2]["refused_by"].startswith("ceiling: 2 records")
