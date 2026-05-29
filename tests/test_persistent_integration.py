"""Integration tests for Persistent Memory inside the Control Loop.

MVP-5 acceptance criteria (the seven the user enumerated):

  1. Agent can write a MemoryRecord to disk.
  2. Agent can read a MemoryRecord in a fresh session.
  3. Memory Write Policy decides save / reject (both branches exercised).
  4. Forbidden data is NOT saved (secrets, no-consent).
  5. There is a command to inspect memory (`agent.list_persistent()`).
  6. There is a command to delete memory (`agent.forget()`).
  7. Tests prove save, load, reject, and delete.

These tests exercise everything through the AgentLoop facade so the CLI
behaviour is covered without spinning up the REPL.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.memory_policy import MemoryRetrievalPolicy, MemoryWritePolicy
from core.persistent_memory import PersistentMemoryStore
from core.planner import LLMPlanner
from core.policy import PolicyGate
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tests.conftest import FakeLLM


def _events(log_path: Path) -> list[dict]:
    events = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _build_agent(
    workspace: Path,
    llm: FakeLLM,
    persistent_path: Path,
    with_memory: bool = True,
):
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    policy = PolicyGate(registry)
    planner = LLMPlanner(llm=llm, registry=registry)
    memory = WorkingMemory() if with_memory else None
    trace_id = new_trace_id()
    logger = TraceLogger(
        trace_id=trace_id,
        log_dir=workspace / "logs",
        verbose=False,
    )
    store = PersistentMemoryStore(persistent_path)
    agent = AgentLoop(
        registry=registry,
        policy=policy,
        llm=llm,
        logger=logger,
        planner=planner,
        memory=memory,
        persistent_store=store,
        retrieval_policy=MemoryRetrievalPolicy(),
        write_policy=MemoryWritePolicy(),
    )
    log_path = workspace / "logs" / f"{trace_id}.jsonl"
    return agent, store, log_path


# ============================================================
# Acceptance #1, #3: agent.remember() saves through the policy
# ============================================================

class TestRememberSaves:
    def test_user_explicit_record_lands_on_disk(self, workspace: Path):
        path = workspace / "data" / "mem.jsonl"
        agent, store, log_path = _build_agent(workspace, FakeLLM(), path)

        decision, record = agent.remember(
            content="User prefers concise answers in Russian.",
            tags=["preference"],
            source="user-explicit",
        )

        assert decision.decision == "save"
        assert record is not None
        assert path.exists()

        # The record is actually on disk and round-trips correctly.
        on_disk = store.load()
        assert len(on_disk) == 1
        assert on_disk[0].id == record.id
        assert on_disk[0].content == "User prefers concise answers in Russian."
        assert on_disk[0].tags == ["preference"]
        assert on_disk[0].owner == "user"

        # And a trace event was emitted with decision=save.
        events = _events(log_path)
        writes = [e for e in events if e["event"] == "persistent_memory_write"]
        assert len(writes) == 1
        assert writes[0]["payload"]["decision"] == "save"
        assert writes[0]["payload"]["record_id"] == record.id


# ============================================================
# Acceptance #2: a fresh session reads records the previous one wrote
# ============================================================

class TestSessionPersistence:
    def test_new_agent_sees_records_from_previous_session(self, workspace: Path):
        path = workspace / "data" / "mem.jsonl"

        # Session A — saves a record, then disappears.
        agent_a, _, _ = _build_agent(workspace, FakeLLM(), path)
        decision, rec = agent_a.remember(
            content="Architecture decision: planner stays LLM-driven, executor deterministic.",
            tags=["decision"],
            source="user-explicit",
        )
        assert decision.decision == "save"
        original_id = rec.id

        # Session B — brand new AgentLoop, same on-disk path.
        agent_b, _, _ = _build_agent(workspace, FakeLLM(), path)
        loaded = agent_b.list_persistent()

        assert len(loaded) == 1
        assert loaded[0].id == original_id
        assert "planner stays LLM-driven" in loaded[0].content


# ============================================================
# Acceptance #4: forbidden data is not saved
# ============================================================

class TestRememberRejects:
    def test_secret_pattern_rejected_no_disk_write(self, workspace: Path):
        path = workspace / "data" / "mem.jsonl"
        agent, store, log_path = _build_agent(workspace, FakeLLM(), path)

        decision, record = agent.remember(
            content="OPENAI_KEY = sk-abcdefghijklmnopqrstuvwxyz0123",
            tags=["fact"],
            source="user-explicit",
        )

        assert decision.decision == "reject"
        assert record is None
        assert store.count() == 0
        # File MAY not exist at all — that's fine — but if it does, it must be empty.
        if path.exists():
            assert path.read_text(encoding="utf-8").strip() == ""

        events = _events(log_path)
        writes = [e for e in events if e["event"] == "persistent_memory_write"]
        assert len(writes) == 1
        assert writes[0]["payload"]["decision"] == "reject"
        assert any("secret" in r for r in writes[0]["payload"]["reasons"])

    def test_no_consent_no_disk_write(self, workspace: Path):
        path = workspace / "data" / "mem.jsonl"
        agent, store, _ = _build_agent(workspace, FakeLLM(), path)

        decision, record = agent.remember(
            content="Some random observation no one asked to keep.",
            tags=["misc"],            # not in CONSENT_TAGS
            source="agent-auto",
        )
        assert decision.decision == "reject"
        assert record is None
        assert store.count() == 0


# ============================================================
# Acceptance #5, #6: list + forget
# ============================================================

class TestListAndForget:
    def test_list_persistent_returns_saved_records(self, workspace: Path):
        path = workspace / "data" / "mem.jsonl"
        agent, _, _ = _build_agent(workspace, FakeLLM(), path)

        agent.remember(content="alpha is one", tags=["fact"], source="user-explicit")
        agent.remember(content="bravo is two", tags=["fact"], source="user-explicit")
        listed = agent.list_persistent()

        contents = [r.content for r in listed]
        assert contents == ["alpha is one", "bravo is two"]

    def test_forget_one_removes_only_that_record(self, workspace: Path):
        path = workspace / "data" / "mem.jsonl"
        agent, store, log_path = _build_agent(workspace, FakeLLM(), path)

        _, r1 = agent.remember(content="keep me", tags=["fact"], source="user-explicit")
        _, r2 = agent.remember(content="drop me", tags=["fact"], source="user-explicit")
        _, r3 = agent.remember(content="keep me too", tags=["fact"], source="user-explicit")

        assert agent.forget(record_id=r2.id) == 1
        remaining = {r.id for r in agent.list_persistent()}
        assert remaining == {r1.id, r3.id}

        events = _events(log_path)
        deletes = [e for e in events if e["event"] == "persistent_memory_delete"]
        assert deletes[-1]["payload"] == {
            "scope": "one",
            "record_id": r2.id,
            "deleted": 1,
        }

    def test_forget_all_wipes_store(self, workspace: Path):
        path = workspace / "data" / "mem.jsonl"
        agent, store, log_path = _build_agent(workspace, FakeLLM(), path)

        agent.remember(content="alpha", tags=["fact"], source="user-explicit")
        agent.remember(content="bravo", tags=["fact"], source="user-explicit")

        n = agent.forget(record_id=None)
        assert n == 2
        assert agent.list_persistent() == []
        assert store.count() == 0

        events = _events(log_path)
        deletes = [e for e in events if e["event"] == "persistent_memory_delete"]
        assert any(
            e["payload"]["scope"] == "all" and e["payload"]["deleted"] == 2
            for e in deletes
        )

    def test_forget_unknown_id_emits_zero_deleted(self, workspace: Path):
        path = workspace / "data" / "mem.jsonl"
        agent, _, log_path = _build_agent(workspace, FakeLLM(), path)
        agent.remember(content="keep me", tags=["fact"], source="user-explicit")

        n = agent.forget(record_id="mem_nope")
        assert n == 0

        events = _events(log_path)
        deletes = [e for e in events if e["event"] == "persistent_memory_delete"]
        assert deletes[-1]["payload"]["deleted"] == 0


# ============================================================
# Loop-level injection: persistent_memory_inject fires when records match
# ============================================================

PLAN_EMPTY = json.dumps({"reasoning": "answerable without tools", "steps": []})
SYNTH_OK = (
    "Conclusion: based on memory [memory:demo].\n"
    "Facts:\n- recorded preference [memory:demo]\n"
    "Sources:\n1. memory:demo - long_term_memory\n"
    "Confidence: medium\nUnverified: nothing\n"
)


class TestRetrievalInjection:
    def test_inject_event_fires_when_question_overlaps(self, workspace: Path):
        path = workspace / "data" / "mem.jsonl"

        # Session A — save a juicy preference.
        agent_a, _, _ = _build_agent(workspace, FakeLLM(), path)
        agent_a.remember(
            content="User prefers Python over JavaScript for backend services.",
            tags=["preference"],
            source="user-explicit",
        )

        # Session B — fresh agent, asks something that overlaps with that record.
        llm = FakeLLM(responses=[PLAN_EMPTY, SYNTH_OK])
        agent_b, _, log_path_b = _build_agent(workspace, llm, path)
        agent_b.run(user_question="What programming language does the user prefer?")

        events = _events(log_path_b)
        injects = [e for e in events if e["event"] == "persistent_memory_inject"]
        assert len(injects) == 1
        payload = injects[0]["payload"]
        assert payload["records_total"] == 1
        assert payload["records_selected"] == 1
        assert payload["chars"] > 0

        # The synthesizer prompt must contain a <long_term_memory> block.
        synth_calls = [c for c in llm.calls if "research analyst" in c["system"]]
        assert any("<long_term_memory>" in c["user"] for c in synth_calls)

    def test_no_inject_when_no_records(self, workspace: Path):
        path = workspace / "data" / "mem.jsonl"

        llm = FakeLLM(responses=[PLAN_EMPTY, SYNTH_OK])
        agent, _, log_path = _build_agent(workspace, llm, path)
        agent.run(user_question="What is the meaning of life?")

        events = _events(log_path)
        injects = [e for e in events if e["event"] == "persistent_memory_inject"]
        assert injects == []

        # Synthesizer prompt must NOT contain a long-term memory block.
        synth_calls = [c for c in llm.calls if "research analyst" in c["system"]]
        for c in synth_calls:
            assert "<long_term_memory>" not in c["user"]

    def test_no_overlap_reports_zero_selected(self, workspace: Path):
        path = workspace / "data" / "mem.jsonl"

        agent_a, _, _ = _build_agent(workspace, FakeLLM(), path)
        agent_a.remember(
            content="Banana cherry date eggplant fig grape",
            tags=["fact"],
            source="user-explicit",
        )

        llm = FakeLLM(responses=[PLAN_EMPTY, SYNTH_OK])
        agent_b, _, log_path = _build_agent(workspace, llm, path)
        agent_b.run(user_question="Tell me about quantum mechanics.")

        events = _events(log_path)
        injects = [e for e in events if e["event"] == "persistent_memory_inject"]
        assert len(injects) == 1
        assert injects[0]["payload"]["records_selected"] == 0

        # No <long_term_memory> block injected into the synthesizer prompt.
        synth_calls = [c for c in llm.calls if "research analyst" in c["system"]]
        for c in synth_calls:
            assert "<long_term_memory>" not in c["user"]


# ============================================================
# Sanity: no persistent_store wired → no persistent_* events
# ============================================================

def test_no_store_means_no_persistent_events(workspace: Path):
    (workspace / "doc.txt").write_text("alpha\n", encoding="utf-8")

    llm = FakeLLM(
        responses=[
            json.dumps(
                {
                    "reasoning": "Read the file.",
                    "steps": [{"tool": "file_read", "arguments": {"path": "doc.txt"}}],
                }
            ),
            "Conclusion: alpha. [file:doc.txt]\nFacts:\n- alpha [file:doc.txt]\n"
            "Sources:\n1. file:doc.txt - doc.txt\nConfidence: high\nUnverified: nothing\n",
        ]
    )

    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=workspace))
    policy = PolicyGate(registry)
    planner = LLMPlanner(llm=llm, registry=registry)
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=registry,
        policy=policy,
        llm=llm,
        logger=logger,
        planner=planner,
        memory=None,
        persistent_store=None,   # disabled
    )
    log_path = workspace / "logs" / f"{trace_id}.jsonl"

    agent.run(user_question="What is in doc.txt?", file_hint="doc.txt")

    events = _events(log_path)
    persistent_events = [e for e in events if e["event"].startswith("persistent_memory_")]
    assert persistent_events == []

    # And remember() with no store wired must reject cleanly.
    decision, record = agent.remember(content="anything", tags=["fact"], source="user-explicit")
    assert decision.decision == "reject"
    assert record is None
    assert any("not configured" in r for r in decision.reasons)
