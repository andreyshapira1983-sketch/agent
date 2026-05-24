"""
tests/brain/test_episodic_memory.py
"""
import pytest
from brain.memory.episodic_memory import EpisodicMemory


@pytest.fixture
def mem(tmp_path):
    return EpisodicMemory(db_path=str(tmp_path / "test_episodic.db"))


def test_store_and_recall(mem):
    mem.store("s1", "user", "Hello")
    mem.store("s1", "assistant", "Hi")
    result = mem.recall("s1")
    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert result[1]["role"] == "assistant"


def test_recall_empty(mem):
    assert mem.recall("nonexistent") == []


def test_recall_limit(mem):
    for i in range(20):
        mem.store("s1", "user", f"msg{i}")
    result = mem.recall("s1", limit=5)
    assert len(result) == 5
    # Should return the last 5 (oldest-first order)
    assert result[-1]["content"] == "msg19"


def test_forget_session(mem):
    mem.store("s1", "user", "data")
    mem.forget("s1")
    assert mem.recall("s1") == []


def test_store_summary(mem):
    mem.store_summary("s1", "User asked about Python.")
    result = mem.recall("s1")
    assert len(result) == 1
    assert result[0]["role"] == "summary"


def test_recall_recent_across_sessions(mem):
    mem.store("s1", "user", "from s1")
    mem.store("s2", "user", "from s2")
    result = mem.recall_recent(limit=10)
    session_ids = {r["session_id"] for r in result}
    assert "s1" in session_ids
    assert "s2" in session_ids


def test_forget_older_than(mem):
    from datetime import datetime, timedelta
    import sqlite3
    # Insert an old record manually
    old_ts = (datetime.utcnow() - timedelta(days=60)).isoformat()
    with sqlite3.connect(mem._db_path) as conn:
        conn.execute(
            "INSERT INTO episodes (session_id, role, content, ts) VALUES (?,?,?,?)",
            ("old_session", "user", "old message", old_ts),
        )
    deleted = mem.forget_older_than(days=30)
    assert deleted >= 1
    assert mem.recall("old_session") == []


def test_sessions_are_isolated(mem):
    mem.store("s1", "user", "message for s1")
    mem.store("s2", "user", "message for s2")
    s1 = mem.recall("s1")
    s2 = mem.recall("s2")
    # Each session returns only its own messages
    assert all(r["content"] == "message for s1" for r in s1)
    assert all(r["content"] == "message for s2" for r in s2)


def test_recall_returns_required_fields(mem):
    mem.store("s1", "user", "check fields")
    result = mem.recall("s1")
    assert len(result) == 1
    row = result[0]
    assert "role" in row
    assert "content" in row


def test_forget_nonexistent_session_no_error(mem):
    # Should not raise
    mem.forget("does_not_exist")


def test_recall_returns_chronological_order(mem):
    for i in range(5):
        mem.store("s1", "user", f"msg{i}")
    result = mem.recall("s1")
    contents = [r["content"] for r in result]
    assert contents == [f"msg{i}" for i in range(5)]


def test_persistence_across_instances(tmp_path):
    db = str(tmp_path / "persist.db")
    m1 = EpisodicMemory(db_path=db)
    m1.store("s1", "user", "persistent")
    m2 = EpisodicMemory(db_path=db)
    result = m2.recall("s1")
    assert len(result) == 1
    assert result[0]["content"] == "persistent"
