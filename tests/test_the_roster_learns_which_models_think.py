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

import core.llm as llm_module
from core.llm import (
    LLM,
    is_known_reasoning_model,
    remember_reasoning_model,
)
from core.state_integrity import read_state_jsonl


def _roster_at(tmp_path, monkeypatch):
    path = tmp_path / "reasoning_roster.jsonl"
    monkeypatch.setenv("AGENT_REASONING_ROSTER", str(path))
    return path


def test_an_unknown_model_is_not_called_a_reasoner(tmp_path, monkeypatch):
    _roster_at(tmp_path, monkeypatch)

    assert is_known_reasoning_model("deepseek", "deepseek-v4-pro") is False


def test_silence_at_the_ceiling_is_remembered(tmp_path, monkeypatch):
    _roster_at(tmp_path, monkeypatch)

    remember_reasoning_model("deepseek", "deepseek-v4-pro", spent=2048)

    assert is_known_reasoning_model("deepseek", "deepseek-v4-pro") is True


def test_the_lesson_does_not_spill_onto_the_sibling_model(tmp_path, monkeypatch):
    _roster_at(tmp_path, monkeypatch)

    remember_reasoning_model("deepseek", "deepseek-v4-pro", spent=2048)

    assert is_known_reasoning_model("deepseek", "deepseek-v4-flash") is False


def test_without_a_home_the_roster_writes_nothing(tmp_path, monkeypatch):
    """A library must not pick a store: the runtime does (see `main.py`).

    Measured 2026-08-29: with a default location baked in, the suite's own
    truncation tests banked three invented models into the live journal, and
    the budget tests then read 8192 where they had asked for 1024.
    """
    monkeypatch.delenv("AGENT_REASONING_ROSTER", raising=False)
    monkeypatch.chdir(tmp_path)

    remember_reasoning_model("deepseek", "deepseek-v4-pro", spent=2048)

    assert list(tmp_path.rglob("*.jsonl")) == []
    assert is_known_reasoning_model("deepseek", "deepseek-v4-pro") is False


def test_a_missing_roster_is_not_a_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AGENT_REASONING_ROSTER", str(tmp_path / "never-written" / "roster.jsonl"),
    )

    assert is_known_reasoning_model("deepseek", "deepseek-v4-pro") is False


def test_remembering_twice_keeps_one_row(tmp_path, monkeypatch):
    path = _roster_at(tmp_path, monkeypatch)

    remember_reasoning_model("deepseek", "deepseek-v4-pro", spent=2048)
    remember_reasoning_model("deepseek", "deepseek-v4-pro", spent=8192)

    rows = read_state_jsonl(path)
    assert [row["key"] for row in rows] == ["deepseek:deepseek-v4-pro"]


class TestTheBudgetFollowsWhatWasLearned:
    """The point of remembering: the next run starts above the floor."""

    def _llm(self, monkeypatch):
        monkeypatch.setattr(LLM, "_build_client", lambda self: None)
        return LLM(provider="deepseek", model="deepseek-v4-pro")

    def test_before_the_lesson_the_budget_stays_where_the_caller_put_it(
        self, tmp_path, monkeypatch,
    ):
        _roster_at(tmp_path, monkeypatch)
        client = self._llm(monkeypatch)

        assert client._effective_budget(2048) == 2048

    def test_after_the_lesson_the_reasoning_floor_applies(self, tmp_path, monkeypatch):
        _roster_at(tmp_path, monkeypatch)
        client = self._llm(monkeypatch)
        remember_reasoning_model("deepseek", "deepseek-v4-pro", spent=2048)

        assert client._effective_budget(2048) == llm_module._REASONING_TOKEN_FLOOR

    def test_a_caller_asking_for_more_than_the_floor_keeps_its_own_number(
        self, tmp_path, monkeypatch,
    ):
        _roster_at(tmp_path, monkeypatch)
        client = self._llm(monkeypatch)
        remember_reasoning_model("deepseek", "deepseek-v4-pro", spent=2048)

        generous = llm_module._REASONING_TOKEN_FLOOR * 2
        assert client._effective_budget(generous) == generous
