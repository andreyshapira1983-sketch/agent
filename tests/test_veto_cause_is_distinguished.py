"""A target must not be avoided because the agent's own generator broke.

`recently_vetoed_self_build_targets` is a cooldown: a file whose last self-build
attempt was critic-vetoed is skipped on the next run, so the producer advances
to another candidate instead of hitting the same wall. That is right when the
veto was a judgement about the target — and wrong when the veto happened
because the builder's reply never parsed.

Measured 2026-08-04 against the agent's own episodic memory
(`data/episodic_memory.jsonl`), all five `critic_veto` episodes present:

    3 × "builder reply did not parse into usable content (raw_chars=…)"
    1 × "empty generated content; no targeted tests specified"
    1 × "confidence 0.02 below threshold 0.60"

Four of the five blamed the target for a broken pipeline, and all five put the
file on the avoid list. The agent was teaching itself to stop touching the files
its own generator choked on — which are exactly the files it needs to work on.

The veto strings below are copied from those real episodes.
"""
from __future__ import annotations

from typing import Any

from core.self_build_memory import (
    build_self_build_episode,
    recently_vetoed_self_build_targets,
    veto_blames_the_target,
)

_PIPELINE_BROKE = (
    "builder reply did not parse into usable content (raw_chars=47830; "
    "JSON невалиден: Expecting ',' delimiter (позиция 32440)); "
    "no targeted tests specified; confidence 0.00 below threshold 0.60"
)
_EMPTY_REPLY = (
    "empty generated content; no targeted tests specified; "
    "confidence 0.00 below threshold 0.60"
)
_REAL_JUDGEMENT = "confidence 0.02 below threshold 0.60"


class _Episode:
    def __init__(self, tags: tuple[str, ...], summary: str = ""):
        self.tags = tags
        self.summary = summary


class _Store:
    """Only what the reader uses: tag search over a fixed list."""

    def __init__(self, episodes: list[_Episode]):
        self.episodes = episodes

    def search_by_tags(self, tags: list[str], limit: int = 20) -> list[Any]:
        wanted = set(tags)
        return [e for e in self.episodes if wanted.issubset(set(e.tags))][:limit]


class _Agent:
    def __init__(self, episodes: list[_Episode]):
        self.episodic_store = _Store(episodes)


def test_a_parse_failure_is_not_a_verdict_on_the_target():
    assert veto_blames_the_target(_PIPELINE_BROKE) is False


def test_an_empty_reply_is_not_a_verdict_on_the_target():
    assert veto_blames_the_target(_EMPTY_REPLY) is False


def test_a_confidence_veto_is_a_verdict_on_the_target():
    assert veto_blames_the_target(_REAL_JUDGEMENT) is True


def test_the_episode_records_which_kind_of_veto_it_was():
    episode = build_self_build_episode("self-build-produce", {
        "status": "critic_veto",
        "target_path": "core/smart_memory.py",
        "reason": _PIPELINE_BROKE,
        "veto_reasons": [_PIPELINE_BROKE],
    })

    assert "veto_pipeline" in episode.tags
    assert "veto_judgement" not in episode.tags


def test_a_real_veto_is_recorded_as_a_judgement():
    episode = build_self_build_episode("self-build-produce", {
        "status": "critic_veto",
        "target_path": "core/smart_memory.py",
        "reason": _REAL_JUDGEMENT,
        "veto_reasons": [_REAL_JUDGEMENT],
    })

    assert "veto_judgement" in episode.tags
    assert "veto_pipeline" not in episode.tags


def test_a_target_vetoed_by_a_broken_pipeline_is_not_avoided():
    agent = _Agent([
        _Episode(("self-build", "critic_veto", "veto_pipeline", "core/smart_memory.py"),
                 _PIPELINE_BROKE),
    ])

    assert recently_vetoed_self_build_targets(agent) == frozenset()


def test_a_target_the_critic_actually_judged_is_still_avoided():
    agent = _Agent([
        _Episode(("self-build", "critic_veto", "veto_judgement", "core/loop.py"),
                 _REAL_JUDGEMENT),
    ])

    assert recently_vetoed_self_build_targets(agent) == frozenset({"core/loop.py"})


def test_episodes_banked_before_this_fix_are_judged_by_their_summary():
    """Old rows carry no classification tag; they must not be trusted blindly."""
    agent = _Agent([
        _Episode(("self-build", "critic_veto", "core/smart_memory.py"), _PIPELINE_BROKE),
        _Episode(("self-build", "critic_veto", "core/loop.py"), _REAL_JUDGEMENT),
    ])

    assert recently_vetoed_self_build_targets(agent) == frozenset({"core/loop.py"})
