"""MVP-14.3c — Knowledge Pipeline Integration tests."""

from __future__ import annotations

import json
from pathlib import Path

from core.approval import AutoApprover
from core.evidence import ProvenanceChain, make_evidence
from core.knowledge_pipeline import (
    ClaimExtractor,
    ConflictResolver,
    KnowledgePipeline,
    KnowledgeWritePolicy,
)
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory_policy import MemoryWritePolicy
from core.persistent_memory import PersistentMemoryStore
from core.policy import PolicyGate
from core.source_ranker import rank_chain
from core.source_registry import SourceRegistry
from core.source_registry_store import SourceRegistryStore
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


def _events(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _file_ev(path: str, text: str):
    return make_evidence(
        kind="file",
        source_id=f"file:{path}",
        obtained_via="file_read",
        claim=f"Contents of workspace file {path}",
        excerpt=text,
        confidence=0.90,
    )


def test_source_registry_store_roundtrips_and_dedupes(tmp_path: Path):
    chain = ProvenanceChain()
    chain.add(_file_ev("doc.txt", "Agent codename is Anya."))
    ranking = rank_chain(chain, question="What is the codename?")
    registry, _ = KnowledgePipeline().build_registry(chain, ranking=ranking)

    store = SourceRegistryStore(tmp_path / "data" / "sources.jsonl")
    first = store.save_registry(registry)
    second = store.save_registry(registry)

    assert first["sources_saved"] == 1
    assert first["claims_saved"] == 1
    assert second["sources_saved"] == 0
    assert second["claims_saved"] == 0
    loaded = store.load_registry()
    assert len(loaded.sources) == 1
    assert len(loaded.claims) == 1
    assert loaded.claims[0].text == "Agent codename is Anya."


def test_claim_extractor_extracts_sentence_claims_not_generic_source_claims():
    evidence = _file_ev(
        "notes.txt",
        "Agent memory stores verified claims. API_KEY=sk-abcdefghijklmnopqrstuvwxyz0123.",
    )
    source = SourceRegistry.from_provenance(ProvenanceChain()).register_source(
        type="file",
        title="notes.txt",
        locator="notes.txt",
        trust_level=0.90,
    )

    claims = ClaimExtractor().extract(evidence, source=source, rank=None)

    assert [c.text for c in claims] == ["Agent memory stores verified claims."]


def test_conflict_resolver_marks_same_subject_different_values():
    registry = SourceRegistry()
    a = registry.register_source(type="file", title="a", locator="a.txt", trust_level=0.9)
    b = registry.register_source(type="file", title="b", locator="b.txt", trust_level=0.9)
    registry.register_claim(source_id=a.id, text="Agent mode is local.", confidence=0.9)
    registry.register_claim(source_id=b.id, text="Agent mode is cloud.", confidence=0.9)

    resolved, report = ConflictResolver().resolve(registry)

    assert report.count == 1
    assert {c.status for c in resolved.claims} == {"conflicted"}
    assert all(c.conflict_source_ids for c in resolved.claims)


def test_conflict_resolver_ignores_generic_this_subject():
    registry = SourceRegistry()
    a = registry.register_source(type="file", title="a", locator="a.txt", trust_level=0.9)
    b = registry.register_source(type="file", title="b", locator="b.txt", trust_level=0.9)
    registry.register_claim(source_id=a.id, text="This is the bridge between answer and learning.", confidence=0.9)
    registry.register_claim(source_id=b.id, text="This is the control loop from the architecture.", confidence=0.9)

    resolved, report = ConflictResolver().resolve(registry)

    assert report.count == 0
    assert {c.status for c in resolved.claims} == {"extracted"}


def test_knowledge_write_policy_rejects_unverified_and_accepts_strong_claim():
    registry = SourceRegistry()
    source = registry.register_source(type="documentation", title="docs", locator="docs", trust_level=0.82)
    good = registry.register_claim(
        source_id=source.id,
        text="Agent tools require policy approval for irreversible actions.",
        confidence=0.80,
    )
    weak = registry.register_claim(
        source_id=source.id,
        text="A forum might be right.",
        confidence=0.30,
        status="unverified",
    )
    policy = KnowledgeWritePolicy()

    assert policy.decide(good, source=source).decision == "save"
    rejected = policy.decide(weak, source=source)
    assert rejected.decision == "reject"
    assert "unverified" in rejected.reasons[0]


def test_agent_loop_persists_sources_and_writes_verified_knowledge(tmp_path: Path):
    (tmp_path / "doc.txt").write_text("Agent codename is Anya.", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=tmp_path))
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=tmp_path / "logs", verbose=False)
    memory_store = PersistentMemoryStore(tmp_path / "data" / "memory.jsonl")
    source_store = SourceRegistryStore(tmp_path / "data" / "sources.jsonl")
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=["Conclusion:\n  ok\nFacts:\n  - ok [file:doc.txt]\nSources:\n  1. file:doc.txt - doc.txt\nConfidence: high\nUnverified:\n  nothing\nSafety:\n  nothing"]),
        logger=logger,
        planner=FakePlanner([{
            "tool": "file_read",
            "arguments": {"path": "doc.txt"},
            "label": "file:doc.txt",
            "expected_outcome": "read doc",
        }]),
        persistent_store=memory_store,
        write_policy=MemoryWritePolicy(),
        source_registry_store=source_store,
        knowledge_auto_write=True,
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
    )

    agent.run("What is the agent codename?", file_hint="doc.txt")

    loaded_sources = source_store.load_registry()
    assert len(loaded_sources.sources) == 1
    assert [claim.text for claim in loaded_sources.claims] == ["Agent codename is Anya."]
    memories = memory_store.load()
    assert len(memories) == 1
    assert "Agent codename is Anya." in memories[0].content
    assert "source-backed" in memories[0].tags
    events = _events(tmp_path / "logs" / f"{trace_id}.jsonl")
    assert any(e["event"] == "knowledge_pipeline" for e in events)
    kp = [e for e in events if e["event"] == "knowledge_pipeline"][-1]["payload"]
    assert kp["memory_saved"] == 1
