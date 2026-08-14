"""A run's own detectors firing is the signal; the words it used are not.

Background: docs/CODE_NOTES.md, "A defect the word table could not see".
"""
from __future__ import annotations

from pathlib import Path

import pytest

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


def test_a_clean_run_is_not_filed(tmp_path: Path):
    """No signal, no issue. Absence of detectors is not a defect."""
    agent = _agent_with(tmp_path, EpisodeRecord(
        goal="answer the operator",
        question="сколько позиций в файле",
        outcome="success",
        summary="42",
        defect_signals=[],
    ))

    assert _recent_self_improvement_events(agent, tmp_path) == []


def test_the_old_word_route_still_admits(tmp_path: Path):
    """The existing route is widened, not replaced."""
    agent = _agent_with(tmp_path, EpisodeRecord(
        goal="produce self-build patch",
        question="self-build-produce",
        outcome="failed",
        summary="self-apply rolled_back: duplicate base class after the split",
    ))

    assert _recent_self_improvement_events(agent, tmp_path)


# Unseen shapes of the same class: a self-repair run that did not succeed, in
# words the table never listed. Measured 2026-08-15 — all three were lost.
@pytest.mark.parametrize("summary", [
    "не удалось: превышен лимит времени на шаге",
    "операция не разрешена политикой, ждём оператора",
    "модель вернула пустоту, содержимого нет",
])
def test_a_failure_worded_unlike_the_table_still_files(tmp_path: Path, summary: str):
    """`outcome` is the structural fact; the words are the author's choice.

    Admission asked whether the text said "rolled_back"/"failed"/"rejected"/
    "duplicate base class"/"too many lines". A self-build run that timed out,
    was refused by policy, or came back empty says none of those.
    """
    agent = _agent_with(tmp_path, EpisodeRecord(
        goal="produce self-build patch",
        question="self-build-produce",
        outcome="partial",
        summary=summary,
    ))

    assert _recent_self_improvement_events(agent, tmp_path), (
        f"a self-repair run that did not succeed was lost: {summary!r}"
    )


def test_a_successful_self_repair_run_is_not_filed(tmp_path: Path):
    """Not-success is the signal. Success is not a defect."""
    agent = _agent_with(tmp_path, EpisodeRecord(
        goal="produce self-build patch",
        question="self-build-produce",
        outcome="success",
        summary="патч создан и одобрен",
    ))

    assert _recent_self_improvement_events(agent, tmp_path) == []
