"""Tests for the OpenAIAdapter system-prompt composition.

We don't hit the OpenAI API. Instead, we verify the pure helper that
glues persona + JSON envelope rules together.
"""

from __future__ import annotations

from brain.adapters.openai_adapter import (
    SYSTEM_PROMPT,
    _compose_system_prompt,
    _JSON_ENVELOPE_RULES,
)


def test_no_persona_returns_default_system_prompt():
    assert _compose_system_prompt(None) == SYSTEM_PROMPT
    assert _compose_system_prompt("") == SYSTEM_PROMPT
    assert _compose_system_prompt("   ") == SYSTEM_PROMPT


def test_persona_is_prepended_with_envelope_rules():
    persona = "You are Аня, a friendly editor."
    composed = _compose_system_prompt(persona)
    assert composed.startswith("You are Аня, a friendly editor.")
    # JSON rules must still be present so the adapter parser keeps working
    assert _JSON_ENVELOPE_RULES in composed
    # The original full default prompt should NOT also be in there
    assert "reasoning engine" not in composed


def test_persona_strips_whitespace_but_preserves_content():
    composed = _compose_system_prompt("  hello\nworld  ")
    assert "hello\nworld" in composed
    assert "  hello" not in composed  # leading whitespace gone


def test_envelope_rules_describe_required_json_fields():
    # Sanity: the rules block must mention every action keyword the
    # parser relies on, otherwise the persona could produce JSON the
    # rest of the pipeline can't handle.
    for keyword in ("action", "content", "confidence", "reasoning",
                    "respond", "tool_call", "wait", "clarify", "stop"):
        assert keyword in _JSON_ENVELOPE_RULES
