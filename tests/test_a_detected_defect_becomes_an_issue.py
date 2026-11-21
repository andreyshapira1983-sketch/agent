"""A run's own detectors firing is the signal; the words it used are not.

Background: docs/CODE_NOTES.md, "A defect the word table could not see".
"""
from __future__ import annotations

from pathlib import Path

from core.self_build_memory import _recent_self_improvement_events
from core.smart_memory import EpisodeRecord, EpisodicMemoryStore


class _Agent:
    def __init__(self, store):
        self.episodic_store = store


def _agent_with(tmp_path: Path, episode: EpisodeRecord) -> _Agent:
    store = EpisodicMemoryStore(tmp_path / "episodic.jsonl")
    store.save(episode)
    return _Agent(store)


def test_a_fabricated_citation_reaches_the_registry(tmp_path: Path):
    """Measured live 2026-08-14: the agent invented four sources and its own
    verifier caught it — `defect_signals=['reasoning_action_mismatch',
    'citation_fabricated']`. Nothing durable recorded it, because admission
    asked whether the TEXT contained "self-apply"/"repair"/"splitter"/"mixin",
    and a fabricated citation says none of those words.
    """
    agent = _agent_with(tmp_path, EpisodeRecord(
        goal="answer the operator",
        question="Где у тебя сейчас ошибки?",
        outcome="partial",
        summary="ответ не отправлен: 4 неразрешившихся цитаты",
        defect_signals=["reasoning_action_mismatch", "citation_fabricated"],
    ))

    events = _recent_self_improvement_events(agent, tmp_path)

    assert events, (
        "the run's own detectors fired and nothing durable recorded it; the "
        "same defect cannot be counted, so repetition cannot be noticed"
    )
    assert any("citation_fabricated" in e["text"] for e in events)
