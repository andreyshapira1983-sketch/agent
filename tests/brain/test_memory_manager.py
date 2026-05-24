"""
tests/brain/test_memory_manager.py
"""
import pytest
from brain.memory.memory_manager import MemoryManager


@pytest.fixture
def manager(tmp_path):
    return MemoryManager(
        episodic_db=str(tmp_path / "ep.db"),
        semantic_dir=str(tmp_path / "sem"),
        working_limit=10,
    )


def test_store_and_recall_history(manager):
    manager.store("s1", "user", "Hello")
    manager.store("s1", "assistant", "Hi")
    history = manager.recall_history("s1", limit=10)
    assert len(history) == 2


def test_recall_history_empty(manager):
    assert manager.recall_history("nonexistent") == []


def test_forget_session(manager):
    manager.store("s1", "user", "data")
    manager.forget("s1")
    assert manager.recall_history("s1") == []


def test_learn_and_recall_fact(manager):
    manager.learn_fact("The agent brain controls the LLM")
    facts = manager.recall_facts("LLM brain agent")
    assert len(facts) >= 1


def test_forget_fact(manager):
    fact_id = manager.learn_fact("Temporary knowledge")
    manager.forget_fact(fact_id)
    # After forget, count should be 0
    assert manager._semantic.count() == 0


def test_summarize_session(manager):
    manager.store("s1", "user", "msg1")
    manager.store("s1", "user", "msg2")
    manager.summarize_session("s1", "User asked two questions.")
    # Working memory cleared after summarize
    working = manager._working.recall("s1")
    assert working == []
    # Summary stored in episodic
    episodic = manager._episodic.recall("s1")
    assert any(e["role"] == "summary" for e in episodic)


def test_status(manager):
    status = manager.status()
    assert "working_sessions" in status
    assert "semantic_facts" in status


def test_warm_up_from_episodic_after_restart(tmp_path):
    db = str(tmp_path / "ep.db")
    sem = str(tmp_path / "sem")
    # First manager — store messages
    m1 = MemoryManager(episodic_db=db, semantic_dir=sem)
    m1.store("s1", "user", "persistent message")

    # Second manager — simulates restart (working memory empty)
    m2 = MemoryManager(episodic_db=db, semantic_dir=sem)
    history = m2.recall_history("s1")
    assert len(history) == 1
    assert history[0]["content"] == "persistent message"


def test_recall_facts_empty_query_returns_empty(manager):
    manager.learn_fact("Some relevant fact")
    results = manager.recall_facts("")
    assert results == []


def test_multiple_sessions_in_status(manager):
    manager.store("session_a", "user", "msg")
    manager.store("session_b", "user", "msg")
    status = manager.status()
    # working_sessions is a list of session IDs
    assert len(status["working_sessions"]) >= 2


def test_prune_old_episodes(tmp_path):
    from datetime import datetime, timedelta
    import sqlite3
    db = str(tmp_path / "prune.db")
    sem = str(tmp_path / "sem")
    manager = MemoryManager(episodic_db=db, semantic_dir=sem)

    old_ts = (datetime.utcnow() - timedelta(days=60)).isoformat()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO episodes (session_id, role, content, ts) VALUES (?,?,?,?)",
            ("old", "user", "old data", old_ts),
        )
    deleted = manager.prune_old_episodes(days=30)
    assert deleted >= 1


def test_store_then_recall_order(manager):
    manager.store("s1", "user", "first")
    manager.store("s1", "assistant", "second")
    history = manager.recall_history("s1")
    assert history[0]["content"] == "first"
    assert history[1]["content"] == "second"


def test_forget_clears_both_working_and_episodic(manager):
    manager.store("s1", "user", "to be forgotten")
    manager.forget("s1")
    assert manager.recall_history("s1") == []
