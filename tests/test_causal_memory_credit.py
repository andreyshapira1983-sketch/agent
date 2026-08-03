"""MIR-074, phase 1 — causal credit and dormancy per the operator's ruling.

The ruling (2026-08-03, recorded in the registry entry): the binary
«полезно/мусор» is FALSE; proof of usefulness is the causal chain —
retrieved for the task → changed the answer → independently verified;
bare keyword-match injection earns ~zero; absolute uselessness is
unprovable, so an unused record goes DORMANT (archived, returnable),
never destroyed.

Measured before this phase (registry MIR-074): zero `[memory:…]` citations
in verified answers across the whole journal history, and an auto-'fact'
record was unarchivable by construction (tag weight 0.8 − max idle 0.3 =
floor 0.5 > threshold 0.25).

Contract under test:

* `MemoryRecord.causal_use` exists, defaults to 0, round-trips the store;
* a `[memory:<id>]` citation whose chunk the verifier marks `verified`
  increments the record's `causal_use` (STRONG credit) and journals
  `memory_causal_credit`; an uncited turn leaves it at 0;
* dry-run/suppressed profiles write no credit;
* hygiene: an agent-auto record (`source-backed` tag) with `causal_use == 0`
  CAN be archived after the idle window — the tag floor no longer grants
  immortality; the same record with causal credit stays; curated protected
  tags stay protected;
* every maintenance pass journals a five-point verdict
  (`hygiene_explained`) in the shared five-marker vocabulary.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.approval import AutoApprover
from core.hygiene import archive_low_value_memory
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from tests.conftest import FakeLLM
from tools.base import ToolRegistry
from tools.file_read import FileReadTool

PLAN_EMPTY = json.dumps({"reasoning": "answerable without tools", "steps": []})


def _events(p: Path) -> list[dict]:
    out = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def _build_agent(workspace: Path, llm: FakeLLM, persistent_path: Path):
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    store = PersistentMemoryStore(persistent_path)
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
        persistent_store=store,
        retrieval_policy=MemoryRetrievalPolicy(),
        write_policy=MemoryWritePolicy(),
        approval_provider=AutoApprover(default="approve"),
    )
    return agent, store, workspace / "logs" / f"{trace_id}.jsonl"


def _synth_citing(record_id: str) -> str:
    return (
        f"Conclusion: user prefers Python [memory:{record_id}].\n"
        f"Facts:\n- recorded preference [memory:{record_id}]\n"
        f"Sources:\n1. memory:{record_id} - long_term_memory\n"
        "Confidence: medium\nUnverified: nothing\n"
    )


# ── the field ────────────────────────────────────────────────────────────────

def test_causal_use_defaults_to_zero_and_round_trips(tmp_path):
    store = PersistentMemoryStore(tmp_path / "mem.jsonl")
    rec = MemoryRecord(type="semantic", content="x", tags=["fact"], owner="user")
    assert rec.causal_use == 0
    store.save(rec)
    loaded = store.load()[0]
    assert loaded.causal_use == 0
    store.update(loaded.model_copy(update={"causal_use": 3}))
    assert store.load()[0].causal_use == 3


# ── the strong credit ────────────────────────────────────────────────────────

def test_verified_memory_citation_earns_causal_credit(workspace: Path):
    path = workspace / "data" / "mem.jsonl"
    agent_a, store, _ = _build_agent(workspace, FakeLLM(), path)
    agent_a.remember(
        content="User prefers Python over JavaScript for backend services.",
        tags=["preference"],
        source="user-explicit",
    )
    rec_id = store.load()[0].id

    llm = FakeLLM(responses=[PLAN_EMPTY, _synth_citing(rec_id)])
    agent_b, store_b, log_path = _build_agent(workspace, llm, path)
    agent_b.run(user_question="What programming language does the user prefer?")

    loaded = store_b.load()[0]
    assert loaded.causal_use == 1, (
        "подтверждённая цитата [memory:id] — сильный причинный кредит"
    )
    credits = [
        e for e in _events(log_path) if e.get("event") == "memory_causal_credit"
    ]
    assert len(credits) == 1
    assert credits[0]["payload"]["record_ids"] == [rec_id]


def test_uncited_turn_earns_no_causal_credit(workspace: Path):
    path = workspace / "data" / "mem.jsonl"
    agent_a, store, _ = _build_agent(workspace, FakeLLM(), path)
    agent_a.remember(
        content="User prefers Python over JavaScript for backend services.",
        tags=["preference"],
        source="user-explicit",
    )
    synth_uncited = (
        "Conclusion: probably Python [general-knowledge].\n"
        "Facts:\n- common choice [general-knowledge]\n"
        "Sources:\n1. general-knowledge - general-knowledge\n"
        "Confidence: low\nUnverified: nothing\n"
    )
    llm = FakeLLM(responses=[PLAN_EMPTY, synth_uncited])
    agent_b, store_b, _ = _build_agent(workspace, llm, path)
    agent_b.run(user_question="What programming language does the user prefer?")
    assert store_b.load()[0].causal_use == 0, (
        "вставка без подтверждённой цитаты — не польза (постановление)"
    )


def test_suppressed_profile_writes_no_credit(workspace: Path):
    path = workspace / "data" / "mem.jsonl"
    agent_a, store, _ = _build_agent(workspace, FakeLLM(), path)
    agent_a.remember(
        content="User prefers Python over JavaScript for backend services.",
        tags=["preference"],
        source="user-explicit",
    )
    rec_id = store.load()[0].id
    llm = FakeLLM(responses=[PLAN_EMPTY, _synth_citing(rec_id)])
    agent_b, store_b, _ = _build_agent(workspace, llm, path)
    agent_b.suppress_durable_learning_writes = True
    agent_b.run(user_question="What programming language does the user prefer?")
    assert store_b.load()[0].causal_use == 0, "сухой профиль не оставляет следов"


# ── dormancy: the tag floor no longer grants immortality ────────────────────

def _auto_fact(age_days: int, causal_use: int) -> MemoryRecord:
    created = datetime.now(timezone.utc) - timedelta(days=age_days)
    return MemoryRecord(
        type="semantic",
        content="claim auto-saved from a doc read",
        tags=["fact", "knowledge", "source-backed", "file"],
        owner="self",
        created_at=created,
        causal_use=causal_use,
    )


class _FakeStore:
    def __init__(self, records):
        self._records = list(records)
        self.archived_ids: list[str] = []

    def load(self):
        return list(self._records)

    def archive_record(self, record_id: str) -> bool:
        self.archived_ids.append(record_id)
        self._records = [r for r in self._records if r.id != record_id]
        return True

    def _rewrite(self, records):
        self._records = list(records)


def test_auto_fact_without_causal_credit_goes_dormant():
    store = _FakeStore([_auto_fact(age_days=30, causal_use=0)])
    report = archive_low_value_memory(store)
    assert len(report.archived) == 1, (
        "автозапись-«факт» без причинного кредита обязана уметь засыпать"
    )


def test_auto_fact_with_causal_credit_stays_active():
    store = _FakeStore([_auto_fact(age_days=30, causal_use=2)])
    report = archive_low_value_memory(store)
    assert report.archived == [], "подтверждённо полезное не отодвигается"


def test_curated_lesson_stays_protected():
    rec = MemoryRecord(
        type="semantic",
        content="урок: не расширять объём",
        tags=["lesson"],
        owner="user",
        created_at=datetime.now(timezone.utc) - timedelta(days=90),
    )
    store = _FakeStore([rec])
    report = archive_low_value_memory(store)
    assert report.archived == []


# ── the five-point hygiene verdict ───────────────────────────────────────────

def test_maintenance_pass_explains_itself_in_five_points(workspace: Path):
    path = workspace / "data" / "mem.jsonl"
    llm = FakeLLM(responses=[])
    agent, _store, log_path = _build_agent(workspace, llm, path)
    agent.run_maintenance_pass(dry_run=True)
    events = [e for e in _events(log_path) if e.get("event") == "hygiene_explained"]
    assert len(events) == 1
    text = events[0]["payload"]["full_text"]
    for marker in (
        "Проверял:", "Способ:", "Доказательство:",
        "Непроверенным осталось:", "Уверенность:",
    ):
        assert marker in text
