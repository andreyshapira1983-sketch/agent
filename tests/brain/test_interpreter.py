"""
tests/brain/test_interpreter.py
"""
import pytest
from unittest.mock import MagicMock
from brain.interpreter import Interpreter
from brain.core import ThinkResult


@pytest.fixture
def interpreter():
    return Interpreter()


def test_valid_respond_action(interpreter):
    output = {
        "action": "respond",
        "content": "Hello, user!",
        "confidence": 0.9,
        "reasoning": "Simple greeting",
    }
    result = interpreter.interpret(output, {})
    assert result.action == "respond"
    assert result.content == "Hello, user!"
    assert result.confidence == 0.9
    assert result.needs_human_approval is False


def test_unknown_action_defaults_to_wait(interpreter):
    output = {"action": "do_something_unknown", "content": "x", "confidence": 0.8, "reasoning": "r"}
    result = interpreter.interpret(output, {})
    assert result.action == "wait"


def test_tool_call_requires_approval(interpreter):
    output = {"action": "tool_call", "content": {"tool": "search"}, "confidence": 0.85, "reasoning": "need search"}
    result = interpreter.interpret(output, {})
    assert result.needs_human_approval is True


def test_stop_requires_approval(interpreter):
    output = {"action": "stop", "content": None, "confidence": 1.0, "reasoning": "done"}
    result = interpreter.interpret(output, {})
    assert result.needs_human_approval is True


def test_confidence_clamped(interpreter):
    output = {"action": "respond", "content": "x", "confidence": 99.0, "reasoning": "r"}
    result = interpreter.interpret(output, {})
    assert result.confidence == 1.0

    output["confidence"] = -5.0
    result = interpreter.interpret(output, {})
    assert result.confidence == 0.0


def test_bad_confidence_defaults_to_05(interpreter):
    output = {"action": "respond", "content": "x", "confidence": "not_a_number", "reasoning": "r"}
    result = interpreter.interpret(output, {})
    assert result.confidence == 0.5


def test_missing_fields_use_defaults(interpreter):
    # Minimal output — no confidence, no reasoning
    output = {"content": "hello"}
    result = interpreter.interpret(output, {})
    assert result.action == "respond"
    assert result.confidence == 0.8
    assert result.reasoning == "No reasoning provided"


def test_completely_empty_output(interpreter):
    # Empty output → default action='respond', content=None
    # Interpreter downgrades respond+None-content to 'clarify'
    result = interpreter.interpret({}, {})
    assert isinstance(result, ThinkResult)
    assert result.action in {"clarify", "wait"}


def test_clarify_action_does_not_require_approval(interpreter):
    output = {"action": "clarify", "content": "Please specify.", "confidence": 0.7, "reasoning": "ambiguous"}
    result = interpreter.interpret(output, {})
    assert result.action == "clarify"
    assert result.needs_human_approval is False


def test_wait_action_does_not_require_approval(interpreter):
    output = {"action": "wait", "content": None, "confidence": 0.5, "reasoning": "pending"}
    result = interpreter.interpret(output, {})
    assert result.action == "wait"
    assert result.needs_human_approval is False


def test_reasoning_preserved(interpreter):
    output = {"action": "respond", "content": "ok", "confidence": 0.9, "reasoning": "Clear intent"}
    result = interpreter.interpret(output, {})
    assert result.reasoning == "Clear intent"


def test_content_dict_normalised_to_canonical_shape(interpreter):
    # The Interpreter now flattens every tool_call into {tool_name, params}
    # so PolicyEngine and the executor only need to know one key.
    payload = {"tool": "search", "params": {"query": "Python"}}
    output = {"action": "tool_call", "content": payload, "confidence": 0.85, "reasoning": "needs search"}
    result = interpreter.interpret(output, {})
    assert result.action == "tool_call"
    assert result.content == {"tool_name": "search", "params": {"query": "Python"}}


def test_tool_call_without_tool_name_demoted(interpreter):
    # LLM forgot the tool name → must not silently slip through Policy.
    output = {"action": "tool_call", "content": {"params": {"x": 1}}, "confidence": 0.8, "reasoning": "?"}
    result = interpreter.interpret(output, {})
    assert result.action == "wait"


def test_tool_call_accepts_top_level_tool_name(interpreter):
    output = {
        "action": "tool_call",
        "tool_name": "docx_writer",
        "params": {"path": "a.docx"},
        "confidence": 0.9,
        "reasoning": "write",
    }
    result = interpreter.interpret(output, {})
    assert result.content == {"tool_name": "docx_writer", "params": {"path": "a.docx"}}


def test_content_none_preserved(interpreter):
    output = {"action": "wait", "content": None, "confidence": 0.4, "reasoning": "unsure"}
    result = interpreter.interpret(output, {})
    assert result.content is None


def test_respond_action_no_approval_needed(interpreter):
    output = {"action": "respond", "content": "Hello", "confidence": 0.95, "reasoning": "greeting"}
    result = interpreter.interpret(output, {})
    assert result.needs_human_approval is False
