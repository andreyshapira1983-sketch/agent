"""Silence is evidence: a model that spends the whole budget thinking is remembered.

Measured 2026-08-29: `deepseek-v4-pro` returned ZERO characters at 2048 and at
8192 output tokens — `finish_reason=length` with 8 209 and 31 357 characters of
reasoning and no answer — and only spoke at 32 768. Inside the agent that read
as fifteen empty plans over eighteen minutes: the planner took the empty string
for an empty plan and never complained.

The escalation that should have saved it was already there, and so was the
8 192-token floor for reasoning models — but the floor was gated on a name
table over three providers, and `deepseek` was not one of them. The comment
beside that table states the rule it violates: a generation written into code
recognises exactly one generation and ages into a bug the moment the provider
ships the next.

So the roster is the agent's design: do not keep a list of names, recognise the
behaviour, and REMEMBER what was recognised, so only the first run pays. This
pins both halves — the recognising and the remembering — and the rule that a
missing roster is not a verdict.
"""
from __future__ import annotations

from core.llm import (
    is_known_reasoning_model,
    remember_reasoning_model,
)
from core.state_integrity import read_state_jsonl


def _roster_at(tmp_path, monkeypatch):
    path = tmp_path / "reasoning_roster.jsonl"
    monkeypatch.setenv("AGENT_REASONING_ROSTER", str(path))
    return path


def test_silence_at_the_ceiling_is_remembered(tmp_path, monkeypatch):
    _roster_at(tmp_path, monkeypatch)

    remember_reasoning_model("deepseek", "deepseek-v4-pro", spent=2048)

    assert is_known_reasoning_model("deepseek", "deepseek-v4-pro") is True


def test_remembering_twice_keeps_one_row(tmp_path, monkeypatch):
    path = _roster_at(tmp_path, monkeypatch)

    remember_reasoning_model("deepseek", "deepseek-v4-pro", spent=2048)
    remember_reasoning_model("deepseek", "deepseek-v4-pro", spent=8192)

    rows = read_state_jsonl(path)
    assert [row["key"] for row in rows] == ["deepseek:deepseek-v4-pro"]
