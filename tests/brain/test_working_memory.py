"""
tests/brain/test_working_memory.py
"""
import pytest
from brain.memory.working_memory import WorkingMemory


def test_store_and_recall():
    mem = WorkingMemory(limit=5)
    mem.store("s1", "user", "Hello")
    mem.store("s1", "assistant", "Hi there")
    result = mem.recall("s1")
    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert result[1]["content"] == "Hi there"


def test_limit_evicts_oldest():
    mem = WorkingMemory(limit=3)
    for i in range(5):
        mem.store("s1", "user", f"msg{i}")
    result = mem.recall("s1")
    assert len(result) == 3
    # Oldest (msg0, msg1) should be gone
    contents = [r["content"] for r in result]
    assert "msg0" not in contents
    assert "msg4" in contents


def test_recall_empty_session():
    mem = WorkingMemory()
    assert mem.recall("nonexistent") == []


def test_forget_clears_session():
    mem = WorkingMemory()
    mem.store("s1", "user", "data")
    mem.forget("s1")
    assert mem.recall("s1") == []


def test_recall_limit_parameter():
    mem = WorkingMemory(limit=20)
    for i in range(10):
        mem.store("s1", "user", f"msg{i}")
    result = mem.recall("s1", limit=3)
    assert len(result) == 3
    assert result[-1]["content"] == "msg9"


def test_active_sessions():
    mem = WorkingMemory()
    mem.store("s1", "user", "a")
    mem.store("s2", "user", "b")
    assert set(mem.active_sessions()) == {"s1", "s2"}


def test_size():
    mem = WorkingMemory()
    assert mem.size("s1") == 0
    mem.store("s1", "user", "x")
    mem.store("s1", "user", "y")
    assert mem.size("s1") == 2


def test_sessions_are_isolated():
    mem = WorkingMemory()
    mem.store("s1", "user", "session 1 msg")
    mem.store("s2", "user", "session 2 msg")
    s1 = mem.recall("s1")
    s2 = mem.recall("s2")
    assert all(r["content"] == "session 1 msg" for r in s1)
    assert all(r["content"] == "session 2 msg" for r in s2)


def test_recall_returns_required_fields():
    mem = WorkingMemory()
    mem.store("s1", "user", "check fields")
    result = mem.recall("s1")
    row = result[0]
    assert "role" in row
    assert "content" in row


def test_forget_nonexistent_session_no_error():
    mem = WorkingMemory()
    mem.forget("ghost_session")  # Should not raise


def test_recall_newest_last():
    mem = WorkingMemory(limit=10)
    mem.store("s1", "user", "first")
    mem.store("s1", "user", "last")
    result = mem.recall("s1")
    assert result[-1]["content"] == "last"
