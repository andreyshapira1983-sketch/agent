"""AGENT_OPENAI_REASONING_EFFORT rides gpt-5+/o-series calls, and only them.

Operator ruling 2026-08-17: the decisive autonomy exam runs on the
strongest engine at high/max reasoning; the client had no such knob, so
Sol would have run at provider default. Unset or invalid degrades to {}
— never raises, never leaks onto non-reasoning models.
"""
from __future__ import annotations

from core.llm import _reasoning_effort_kwargs


def test_unset_is_empty(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_OPENAI_REASONING_EFFORT", raising=False)
    assert _reasoning_effort_kwargs() == {}


def test_a_valid_level_rides(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_OPENAI_REASONING_EFFORT", "high")
    assert _reasoning_effort_kwargs() == {"reasoning_effort": "high"}


def test_case_and_whitespace_are_forgiven(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_OPENAI_REASONING_EFFORT", "  MAX ")
    assert _reasoning_effort_kwargs() == {"reasoning_effort": "max"}


def test_garbage_degrades_to_default_not_an_error(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_OPENAI_REASONING_EFFORT", "turbo-ultra")
    assert _reasoning_effort_kwargs() == {}
