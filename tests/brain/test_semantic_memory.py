"""
tests/brain/test_semantic_memory.py
"""
import pytest
from brain.memory.semantic_memory import SemanticMemory


@pytest.fixture
def mem(tmp_path):
    return SemanticMemory(persist_dir=str(tmp_path / "semantic"))


def test_store_and_recall(mem):
    mem.store_fact("Python uses indentation for code blocks")
    results = mem.recall("Python indentation")
    assert len(results) >= 1
    assert any("Python" in r["text"] for r in results)


def test_recall_empty_returns_nothing(mem):
    results = mem.recall("   ")
    assert results == []


def test_count_increases_after_store(mem):
    assert mem.count() == 0
    mem.store_fact("Fact one")
    mem.store_fact("Fact two")
    assert mem.count() == 2


def test_forget_fact(mem):
    fact_id = mem.store_fact("Temporary fact")
    assert mem.count() == 1
    mem.forget_fact(fact_id)
    assert mem.count() == 0


def test_recall_returns_score(mem):
    mem.store_fact("The agent uses LLM as a tool, not as the brain")
    results = mem.recall("LLM tool agent")
    assert len(results) >= 1
    assert "score" in results[0]
    assert 0.0 <= results[0]["score"] <= 1.0


def test_recall_top_k(mem):
    for i in range(10):
        mem.store_fact(f"Fact number {i} about agents and memory systems")
    results = mem.recall("agents memory", top_k=3)
    assert len(results) <= 3


def test_forget_nonexistent_id_no_error(mem):
    mem.forget_fact("nonexistent-id-xyz")  # Should not raise


def test_store_returns_id(mem):
    fact_id = mem.store_fact("Returns an ID")
    assert isinstance(fact_id, str)
    assert len(fact_id) > 0


def test_multiple_facts_all_recalled(mem):
    mem.store_fact("The sky is blue")
    mem.store_fact("The ocean is deep")
    mem.store_fact("Agents use memory")
    assert mem.count() == 3


def test_recall_empty_query_returns_nothing(mem):
    mem.store_fact("Some fact")
    results = mem.recall("")
    assert results == []


def test_recall_score_between_0_and_1(mem):
    mem.store_fact("Agents are autonomous")
    results = mem.recall("autonomous agent")
    for r in results:
        assert 0.0 <= r["score"] <= 1.0
