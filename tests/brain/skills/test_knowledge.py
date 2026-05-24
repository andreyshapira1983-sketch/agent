"""Tests for brain/skills/knowledge.py — KnowledgeBase."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from brain.skills.knowledge import (
    KnowledgeBase,
    KnowledgeFact,
    _chunk,
)


# ════════════════════════════════════════════════════════════════════
# Fake SemanticMemory
# ════════════════════════════════════════════════════════════════════

@dataclass
class FakeSemantic:
    """In-memory stand-in for SemanticMemory with metadata-aware recall."""

    facts: dict[str, dict] = field(default_factory=dict)

    def store_fact(self, text: str, metadata: dict | None = None) -> str:
        fid = uuid.uuid4().hex[:8]
        self.facts[fid] = {"text": text, "metadata": metadata or {}, "score": 1.0}
        return fid

    def recall(self, query: str, top_k: int = 5) -> list[dict]:
        # Naive substring score; metadata.score 1.0 if any query word appears.
        q_words = set(query.lower().split())
        out = []
        for fid, rec in self.facts.items():
            t = rec["text"].lower()
            score = sum(1 for w in q_words if w in t) / max(len(q_words), 1)
            if score > 0:
                out.append({"text": rec["text"], "score": score, "metadata": rec["metadata"]})
        out.sort(key=lambda x: x["score"], reverse=True)
        return out[:top_k]

    def forget_fact(self, fact_id: str) -> None:
        self.facts.pop(fact_id, None)


# ════════════════════════════════════════════════════════════════════
# load_for
# ════════════════════════════════════════════════════════════════════

class TestLoadFor:

    def test_loads_markdown_files(self, tmp_path):
        kdir = tmp_path / "knowledge"
        kdir.mkdir()
        (kdir / "glossary.md").write_text(
            "# Glossary\n\nFreelancer → Фрилансер.\n", encoding="utf-8",
        )
        (kdir / "style.md").write_text(
            "Use short sentences. Prefer active voice.\n",
            encoding="utf-8",
        )
        backend = FakeSemantic()
        kb = KnowledgeBase(backend)
        facts = kb.load_for("text_editor", kdir)
        assert len(facts) == 2
        assert all(isinstance(f, KnowledgeFact) for f in facts)
        assert kb.fact_count("text_editor") == 2

    def test_missing_dir_is_noop(self, tmp_path):
        kb = KnowledgeBase(FakeSemantic())
        out = kb.load_for("nope", tmp_path / "does-not-exist")
        assert out == []
        assert kb.fact_count("nope") == 0

    def test_reload_clears_previous_slice(self, tmp_path):
        kdir = tmp_path / "k"
        kdir.mkdir()
        f = kdir / "one.md"
        f.write_text("first version", encoding="utf-8")

        backend = FakeSemantic()
        kb = KnowledgeBase(backend)
        kb.load_for("p", kdir)
        assert kb.fact_count("p") == 1

        f.write_text("second version\n\nAdditional paragraph.", encoding="utf-8")
        kb.load_for("p", kdir)
        # Should be one chunk (still under MAX_CHUNK_CHARS) but the old facts gone
        assert kb.fact_count("p") == 1
        assert len(backend.facts) == 1
        stored_text = next(iter(backend.facts.values()))["text"]
        assert "second" in stored_text


# ════════════════════════════════════════════════════════════════════
# retrieve
# ════════════════════════════════════════════════════════════════════

class TestRetrieve:

    def test_namespace_filtered(self, tmp_path):
        backend = FakeSemantic()
        kb = KnowledgeBase(backend)

        (tmp_path / "k1").mkdir()
        (tmp_path / "k1" / "a.md").write_text("python loves comprehensions", encoding="utf-8")
        kb.load_for("python_dev", tmp_path / "k1")

        (tmp_path / "k2").mkdir()
        (tmp_path / "k2" / "b.md").write_text("python is also a snake", encoding="utf-8")
        kb.load_for("biology", tmp_path / "k2")

        py_hits = kb.retrieve("python_dev", "python", top_k=5)
        bio_hits = kb.retrieve("biology", "python", top_k=5)
        assert py_hits and "comprehensions" in py_hits[0]["text"]
        assert bio_hits and "snake" in bio_hits[0]["text"]

    def test_empty_query_returns_nothing(self, tmp_path):
        kb = KnowledgeBase(FakeSemantic())
        assert kb.retrieve("any", "") == []
        assert kb.retrieve("any", "   ") == []


# ════════════════════════════════════════════════════════════════════
# chunking
# ════════════════════════════════════════════════════════════════════

class TestChunk:

    def test_short_text_one_chunk(self):
        chunks = _chunk("Hello world.\n\nSecond paragraph.")
        assert chunks == ["Hello world.\n\nSecond paragraph."]

    def test_empty_text_no_chunks(self):
        assert _chunk("") == []
        assert _chunk("   ") == []

    def test_long_text_splits(self):
        para = "x" * 1000
        text = (para + "\n\n") * 3   # 3000 chars + separators
        chunks = _chunk(text)
        assert len(chunks) >= 2
        for c in chunks:
            assert len(c) < 3000
