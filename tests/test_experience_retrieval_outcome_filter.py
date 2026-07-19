"""Regression: experience retrieval must not feed back non-success episodes
as "experience" (CORE-05 / LPF-012).

`_retrieve_experience_memory` injected `episodic_store.search(...)` results with
NO outcome filter, so a `partial`/`failed` episode could be surfaced to the
planner as prior experience — the self-reinforcing loop. Now that CORE-01 marks
majority-unverified answers as `partial`, filtering to `outcome == "success"`
(plus curated `lesson` episodes, which are learn-from-failure by design) is a
principled cut, no threshold.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.loop_methods2 import AgentLoopExtractedMethods2
from core.smart_memory import EpisodeRecord, EpisodicMemoryStore


def _fake_self(store):
    return SimpleNamespace(
        episodic_store=store,
        procedural_store=None,
        log=SimpleNamespace(log=lambda *a, **k: None),
        _last_episode_records=[],
        _last_procedure_records=[],
        _last_best_similar_episode=None,
        _last_best_similar_score=0.0,
    )


def _ep(outcome, summary):
    return EpisodeRecord(
        goal="deploy service",
        question="how to deploy the service now",
        outcome=outcome,
        summary=summary,
        tools_used=("shell_exec",),
        # This suite covers the OUTCOME filter; usage eligibility (2c) is a
        # second, independent gate with its own suite, so seed past it.
        usage_eligible=True,
    )


def test_partial_episode_not_injected_as_experience(tmp_path):
    store = EpisodicMemoryStore(tmp_path / "episodes.jsonl")
    store.save(_ep("success", "deployed ok via script"))
    store.save(_ep("partial", "deploy did not complete"))

    me = _fake_self(store)
    AgentLoopExtractedMethods2._retrieve_experience_memory(me, "how to deploy the service now")

    outcomes = {ep.outcome for ep in me._last_episode_records}
    assert "success" in outcomes, "the successful episode should be injected"
    assert "partial" not in outcomes, "a partial episode must not be fed back as experience"


def test_lesson_episode_kept_even_if_not_success(tmp_path):
    store = EpisodicMemoryStore(tmp_path / "episodes.jsonl")
    lesson = EpisodeRecord(
        goal="deploy service",
        question="how to deploy the service now",
        outcome="partial",
        summary="lesson: never deploy on a stale checkout",
        tools_used=("shell_exec",),
        tags=("episode", "partial", "lesson"),
        usage_eligible=True,
    )
    store.save(lesson)

    me = _fake_self(store)
    AgentLoopExtractedMethods2._retrieve_experience_memory(me, "how to deploy the service now")

    assert any("lesson" in ep.tags for ep in me._last_episode_records), (
        "a curated lesson episode must be kept regardless of outcome"
    )
