"""Ответ, чей верификатор упал, не повторяется быстрым путём, даже если синтез объявил «готово»."""
from __future__ import annotations

from pathlib import Path

import pytest

import core.verifier
from tests.test_fast_path_verification_provenance import _Q, _fast_path_fired, _make_loop


def _bank_as_after_a_verifier_crash(workspace: Path):
    """Записать эпизод ровно так, как цикл пишет его после мягкого сбоя верификатора."""
    loop, _events, store = _make_loop(workspace)
    loop._record_experience_memory(
        goal_description="answer", question=_Q, answer="Sydney.",
        tools_used=[], source_labels=["general-knowledge"],
        verified_chunks=0, unverified_chunks=0, replan_exhausted=False,
        verifier_failure=True, declared_completion="achieved",
    )
    return store


def test_an_episode_from_a_crashed_verifier_is_not_replayed(workspace: Path) -> None:
    """Эпизод после падения верификатора не проходит в быстрый путь."""
    store = _bank_as_after_a_verifier_crash(workspace)

    loop, events, _ = _make_loop(workspace)
    loop.run(_Q)

    assert not _fast_path_fired(events)
    banked = [ep for ep in store.load() if ep.full_answer == "Sydney."]
    assert len(banked) == 1
    assert banked[0].answer_quality_score is None, "пустая проверка не должна давать оценку"
    assert banked[0].usage_eligible is False, "без проверенных фрагментов эпизод не ведёт будущие ответы"


def test_a_live_verifier_crash_is_soft_and_leaves_nothing_replayable(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Живой сбой верификатора не роняет ход, журналируется, и следующий такой же вопрос не берёт ответ из памяти."""
    def _crash(**_kwargs):
        raise RuntimeError("simulated verifier crash")

    monkeypatch.setattr(core.verifier, "verify", _crash)
    loop, events, store = _make_loop(workspace)
    loop.run(_Q)
    assert "verifier_failure" in events
    assert all(ep.answer_quality_score is None for ep in store.load())

    monkeypatch.undo()
    loop2, events2, _ = _make_loop(workspace)
    loop2.run(_Q)

    assert not _fast_path_fired(events2)
