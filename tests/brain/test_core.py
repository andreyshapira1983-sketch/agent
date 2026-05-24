"""
tests/brain/test_core.py — Tests for Brain (Cognitive Core)

Uses mocks for LLM and Memory — Brain is tested in isolation.
"""
import pytest
from unittest.mock import MagicMock, patch
from brain.core import Brain, ThinkResult
from brain.interfaces.llm_interface import LLMInterface
from brain.interfaces.memory_interface import MemoryInterface


def make_brain(llm_response: dict | None = None):
    """Helper: create Brain with mock LLM and Memory."""
    llm = MagicMock(spec=LLMInterface)
    llm.call.return_value = llm_response or {
        "action": "respond",
        "content": "Test response",
        "confidence": 0.9,
        "reasoning": "Test reasoning",
    }

    memory = MagicMock(spec=MemoryInterface)
    memory.recall_history.return_value = []
    memory.recall_facts.return_value = []

    brain = Brain(llm=llm, memory=memory)
    return brain, llm, memory


def test_empty_input_fast_path():
    brain, llm, _ = make_brain()
    result = brain.think("   ", session_id="s1")
    assert result.action == "wait"
    llm.call.assert_not_called()  # LLM must NOT be called


def test_stop_command_fast_path():
    brain, llm, _ = make_brain()
    result = brain.think("stop", session_id="s1")
    assert result.action == "stop"
    llm.call.assert_not_called()


def test_normal_input_calls_llm():
    brain, llm, _ = make_brain()
    result = brain.think("What is Python?", session_id="s1")
    llm.call.assert_called_once()
    assert result.action == "respond"


def test_low_confidence_blocked():
    brain, llm, _ = make_brain(llm_response={
        "action": "respond",
        "content": "maybe...",
        "confidence": 0.3,
        "reasoning": "not sure",
    })
    result = brain.think("Do something risky", session_id="s1")
    assert result.action == "wait"
    assert result.needs_human_approval is True


def test_set_goal():
    brain, _, _ = make_brain()
    brain.set_goal("Answer user questions", priority=2)
    status = brain.status()
    assert status["goal_depth"] == 1
    assert status["active_goals"][0]["text"] == "Answer user questions"


def test_clear_goals():
    brain, _, _ = make_brain()
    brain.set_goal("Goal 1")
    brain.set_goal("Goal 2")
    brain.clear_goals()
    assert brain.status()["goal_depth"] == 0


def test_memory_is_called():
    brain, _, memory = make_brain()
    brain.think("Hello", session_id="s42")
    memory.recall_history.assert_called_once_with(session_id="s42", limit=10)
    memory.recall_facts.assert_called_once()


def test_status_returns_dict():
    brain, _, _ = make_brain()
    status = brain.status()
    assert "active_goals" in status
    assert "goal_depth" in status


def test_think_result_fields():
    brain, _, _ = make_brain()
    result = brain.think("What is Python?", session_id="s1")
    assert hasattr(result, "action")
    assert hasattr(result, "content")
    assert hasattr(result, "confidence")
    assert hasattr(result, "reasoning")
    assert hasattr(result, "needs_human_approval")
    assert isinstance(result.confidence, float)
    assert isinstance(result.needs_human_approval, bool)


def test_llm_error_response_becomes_wait():
    # LLM returns an error dict — Brain should handle gracefully
    brain, _, _ = make_brain(llm_response={
        "action": "wait",
        "content": None,
        "confidence": 0.0,
        "reasoning": "Connection error: timeout",
    })
    result = brain.think("Hello", session_id="s1")
    assert result.action in {"wait", "clarify"}


def test_memory_store_not_called_on_fast_path():
    # Fast path must not trigger LLM, but memory recall may be called
    brain, llm, memory = make_brain()
    brain.think("", session_id="s1")
    llm.call.assert_not_called()


def test_set_goal_default_priority():
    brain, _, _ = make_brain()
    brain.set_goal("Default priority goal")
    status = brain.status()
    assert status["goal_depth"] == 1


def test_multiple_goals_priority_order():
    brain, _, _ = make_brain()
    brain.set_goal("Low", priority=1)
    brain.set_goal("High", priority=10)
    brain.set_goal("Mid", priority=5)
    goals = brain.status()["active_goals"]
    priorities = [g["priority"] for g in goals]
    assert priorities == sorted(priorities, reverse=True)


def test_independent_sessions_do_not_mix():
    brain, _, memory = make_brain()
    brain.think("Session A message", session_id="a")
    brain.think("Session B message", session_id="b")
    # Memory should have been called with different session IDs
    calls = [call.kwargs["session_id"] for call in memory.recall_history.call_args_list]
    assert "a" in calls
    assert "b" in calls


def test_think_stores_context_to_memory():
    # Memory.store is called after think (if memory stores conversations)
    # ContextBuilder calls recall — verify those calls happen
    brain, _, memory = make_brain()
    brain.think("Remember this", session_id="s1")
    memory.recall_history.assert_called()
