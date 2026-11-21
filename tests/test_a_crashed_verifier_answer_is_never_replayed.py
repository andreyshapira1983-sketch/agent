"""Ответ, чей верификатор упал, не повторяется быстрым путём, даже если синтез объявил «готово»."""
from __future__ import annotations

from pathlib import Path

import pytest

import core.verifier
from tests.test_fast_path_verification_provenance import _Q, _fast_path_fired, _make_loop


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
