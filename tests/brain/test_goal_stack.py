"""
tests/brain/test_goal_stack.py
"""
import pytest
from brain.goal_stack import GoalStack


def test_push_and_current():
    gs = GoalStack()
    gs.push("Answer user question", priority=1)
    goals = gs.current()
    assert len(goals) == 1
    assert goals[0]["text"] == "Answer user question"
    assert goals[0]["status"] == "active"


def test_priority_ordering():
    gs = GoalStack()
    gs.push("Low priority", priority=1)
    gs.push("High priority", priority=5)
    gs.push("Medium priority", priority=3)
    goals = gs.current()
    assert goals[0]["priority"] == 5
    assert goals[1]["priority"] == 3
    assert goals[2]["priority"] == 1


def test_depth():
    gs = GoalStack()
    assert gs.depth() == 0
    gs.push("g1")
    gs.push("g2")
    assert gs.depth() == 2


def test_clear():
    gs = GoalStack()
    gs.push("g1")
    gs.push("g2")
    gs.clear()
    assert gs.depth() == 0
    assert gs.current() == []


def test_update_marks_complete_on_stop():
    from unittest.mock import MagicMock
    gs = GoalStack()
    gs.push("Complete task")
    result = MagicMock()
    result.action = "stop"
    gs.update(result)
    # All goals should be marked completed
    active = gs.current()
    assert len(active) == 0


def test_empty_stack_current():
    gs = GoalStack()
    assert gs.current() == []


def test_push_default_priority():
    gs = GoalStack()
    gs.push("Default priority")
    goals = gs.current()
    assert len(goals) == 1
    assert goals[0]["priority"] >= 1


def test_update_respond_completes_top_goal():
    """respond action completes the highest-priority active goal."""
    from unittest.mock import MagicMock
    gs = GoalStack()
    gs.push("Top priority goal", priority=3)
    gs.push("Lower goal", priority=1)
    result = MagicMock()
    result.action = "respond"
    gs.update(result)
    # Highest-priority goal completed; lower goal remains
    assert gs.depth() == 1
    assert gs.current()[0]["text"] == "Lower goal"


def test_update_tool_call_keeps_all_goals():
    """tool_call does NOT auto-complete goals — tool may need multiple steps."""
    from unittest.mock import MagicMock
    gs = GoalStack()
    gs.push("Active goal")
    result = MagicMock()
    result.action = "tool_call"
    gs.update(result)
    assert gs.depth() == 1  # goal still active


def test_same_priority_both_present():
    gs = GoalStack()
    gs.push("Goal A", priority=3)
    gs.push("Goal B", priority=3)
    assert gs.depth() == 2
    goals = gs.current()
    texts = {g["text"] for g in goals}
    assert "Goal A" in texts
    assert "Goal B" in texts


def test_current_returns_dict_with_required_keys():
    gs = GoalStack()
    gs.push("Check keys")
    goal = gs.current()[0]
    assert "text" in goal
    assert "priority" in goal
    assert "status" in goal


def test_clear_then_push_works():
    gs = GoalStack()
    gs.push("First")
    gs.clear()
    gs.push("After clear")
    assert gs.depth() == 1
    assert gs.current()[0]["text"] == "After clear"
