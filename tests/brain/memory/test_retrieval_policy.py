"""Tests for brain/memory/retrieval_policy.py + ContextBuilder integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from brain.context_builder import ContextBuilder
from brain.memory.retrieval_policy import DEFAULT_POLICY, RetrievalPolicy
from brain.skills.knowledge import KnowledgeBase


# ════════════════════════════════════════════════════════════════════
# RetrievalPolicy
# ════════════════════════════════════════════════════════════════════

class TestRetrievalPolicy:

    def test_defaults_are_reasonable(self):
        p = RetrievalPolicy()
        assert p.history_limit == 10
        assert p.facts_top_k == 5
        assert p.profession_namespace is None
        assert p.reserved_profession_slots() == 0
        assert p.universal_slots() == 5

    def test_with_profession(self):
        p = RetrievalPolicy().with_profession("text_editor")
        assert p.profession_namespace == "prof:text_editor"
        assert p.reserved_profession_slots() == 5

    def test_split_slots(self):
        p = RetrievalPolicy(
            facts_top_k=10, profession_namespace="prof:x", profession_facts_top_k=7,
        )
        assert p.reserved_profession_slots() == 7
        assert p.universal_slots() == 3


# ════════════════════════════════════════════════════════════════════
# ContextBuilder with KnowledgeBase
# ════════════════════════════════════════════════════════════════════

@dataclass
class FakeMemory:
    """Implements MemoryInterface-shaped methods used by ContextBuilder."""
    history_data: list[dict] = field(default_factory=list)
    universal_facts: list[dict] = field(default_factory=list)
    last_history_limit: int = 0
    last_facts_top_k: int = 0

    def recall_history(self, session_id: str, limit: int = 10) -> list[dict]:
        self.last_history_limit = limit
        return self.history_data[-limit:]

    def recall_facts(self, query: str, top_k: int = 5) -> list[dict]:
        self.last_facts_top_k = top_k
        return self.universal_facts[:top_k]


@dataclass
class FakeSemantic:
    facts: dict = field(default_factory=dict)

    def store_fact(self, text: str, metadata: dict | None = None) -> str:
        fid = f"f{len(self.facts)}"
        self.facts[fid] = {"text": text, "metadata": metadata or {}}
        return fid

    def recall(self, query: str, top_k: int = 5) -> list[dict]:
        # Return everything that has the requested namespace, scored 1.0
        return [
            {"text": v["text"], "score": 1.0, "metadata": v["metadata"]}
            for v in self.facts.values()
        ][:top_k]

    def forget_fact(self, fid: str) -> None:
        self.facts.pop(fid, None)


class TestContextBuilderRetrieval:

    def test_default_behaviour_unchanged(self):
        memory = FakeMemory(
            history_data=[{"role": "user", "content": "hi"}] * 12,
            universal_facts=[{"text": f"u{i}", "metadata": {}} for i in range(10)],
        )
        builder = ContextBuilder(memory=memory)
        ctx = builder.build(raw_input="q", goals=[], session_id="s")
        # default history_limit=10, facts_top_k=5
        assert len(ctx["history"]) == 10
        assert len(ctx["facts"]) == 5
        assert memory.last_facts_top_k == 5

    def test_profession_namespace_pulls_from_knowledge_base(self, tmp_path):
        memory = FakeMemory(
            universal_facts=[{"text": "universal", "metadata": {}}],
        )
        semantic = FakeSemantic()
        kb = KnowledgeBase(semantic)
        kdir = tmp_path / "k"
        kdir.mkdir()
        (kdir / "g.md").write_text("freelancer = фрилансер", encoding="utf-8")
        kb.load_for("text_editor", kdir)

        policy = RetrievalPolicy(facts_top_k=3).with_profession("text_editor")
        builder = ContextBuilder(memory=memory, knowledge_base=kb, retrieval_policy=policy)
        ctx = builder.build(raw_input="freelancer", goals=[], session_id="s")
        # First fact comes from profession namespace
        assert any("фрилансер" in f["text"] for f in ctx["facts"])

    def test_per_call_policy_overrides_default(self, tmp_path):
        memory = FakeMemory(
            universal_facts=[{"text": "u1", "metadata": {}}] * 5,
        )
        semantic = FakeSemantic()
        kb = KnowledgeBase(semantic)
        kdir = tmp_path / "k"
        kdir.mkdir()
        (kdir / "x.md").write_text("specific knowledge", encoding="utf-8")
        kb.load_for("translator", kdir)

        builder = ContextBuilder(memory=memory, knowledge_base=kb)
        # default builder has no profession; pass one explicitly via build()
        overridden = RetrievalPolicy(facts_top_k=2).with_profession("translator")
        ctx = builder.build(raw_input="specific", goals=[], session_id="s", policy=overridden)
        assert any("specific" in f["text"] for f in ctx["facts"])

    def test_history_limit_respected(self):
        memory = FakeMemory(
            history_data=[{"role": "user", "content": str(i)} for i in range(20)],
        )
        policy = RetrievalPolicy(history_limit=3)
        builder = ContextBuilder(memory=memory, retrieval_policy=policy)
        ctx = builder.build(raw_input="q", goals=[], session_id="s")
        assert len(ctx["history"]) == 3
        assert memory.last_history_limit == 3

    def test_similarity_threshold_filters(self):
        memory = FakeMemory(
            universal_facts=[
                {"text": "high", "score": 0.9, "metadata": {}},
                {"text": "low",  "score": 0.1, "metadata": {}},
            ],
        )
        policy = RetrievalPolicy(facts_top_k=5, similarity_threshold=0.5)
        builder = ContextBuilder(memory=memory, retrieval_policy=policy)
        ctx = builder.build(raw_input="q", goals=[], session_id="s")
        texts = [f["text"] for f in ctx["facts"]]
        assert texts == ["high"]
