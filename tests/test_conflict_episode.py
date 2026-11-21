"""Tests for core.conflict_episode (procedural memory of instruction conflicts).

The properties worth pinning: an episode is banked *before* anyone rules on it,
a ruling never overwrites history, and an unresolved episode can't leak into a
training set as though it had an answer.
"""
from __future__ import annotations

from pathlib import Path

from core.conflict_episode import (
    ConflictEpisode,
    ConflictEpisodeStore,
    default_path,
    episodes_from_outcome,
)
from core.directive_extractor import extract_from_task_and_review
from core.instruction_conflict_gate import evaluate


def _blocked_outcome():
    return evaluate(extract_from_task_and_review(
        task_text="Порядок вывода должен быть стабильным.",
        review_text="Отсортируй по имени.",
    ))


def _episode() -> ConflictEpisode:
    episodes = episodes_from_outcome(
        _blocked_outcome(), context="почини сортировку в отчёте"
    )
    assert episodes
    return episodes[0]


# ---------------------------------------------------------------------------
# Building episodes from a gate outcome
# ---------------------------------------------------------------------------

def test_proceeding_outcome_produces_no_episode():
    outcome = evaluate([])
    assert episodes_from_outcome(outcome) == ()


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


def test_empty_store_reads_empty(tmp_path: Path):
    store = ConflictEpisodeStore(default_path(tmp_path))
    assert store.load_all() == ()
    assert store.load_open() == ()
    assert store.load_recent() == ()


def test_save_many_counts_rows(tmp_path: Path):
    store = ConflictEpisodeStore(default_path(tmp_path))
    assert store.save_many([_episode(), _episode()]) == 2
    assert store.save_many([]) == 0
    assert len(store.load_all()) == 2


def test_resolving_an_unknown_id_returns_none(tmp_path: Path):
    store = ConflictEpisodeStore(default_path(tmp_path))
    assert store.resolve("conflict_nope", ruling="x", ruled_by="y") is None


def test_load_open_excludes_resolved(tmp_path: Path):
    store = ConflictEpisodeStore(default_path(tmp_path))
    kept, closed = _episode(), _episode()
    store.save_many([kept, closed])
    store.resolve(closed.id, ruling="решено", ruled_by="оператор")

    open_ids = {e.id for e in store.load_open()}
    assert kept.id in open_ids
    assert closed.id not in open_ids


def test_load_recent_zero_returns_nothing(tmp_path: Path):
    """A caller computing n from config and getting 0 means "none", not "one"."""
    store = ConflictEpisodeStore(default_path(tmp_path))
    store.save_many([_episode(), _episode()])
    assert store.load_recent(0) == ()
    assert store.load_recent(-5) == ()


def test_load_recent_is_most_recent_first(tmp_path: Path):
    store = ConflictEpisodeStore(default_path(tmp_path))
    first, second = _episode(), _episode()
    store.save_many([first, second])
    assert [e.id for e in store.load_recent(2)] == [second.id, first.id]


# ---------------------------------------------------------------------------
# Dataset rows
# ---------------------------------------------------------------------------

def test_training_rows_exclude_unresolved_episodes(tmp_path: Path):
    store = ConflictEpisodeStore(default_path(tmp_path))
    store.save(_episode())
    assert store.training_rows() == (), (
        "an episode nobody ruled on has no correct answer to learn from"
    )


def test_training_row_carries_instruction_conflict_and_decision(tmp_path: Path):
    store = ConflictEpisodeStore(default_path(tmp_path))
    episode = _episode()
    store.save(episode)
    store.resolve(
        episode.id,
        ruling="приоритет у контракта; сортировку не вносим",
        ruled_by="оператор",
        lesson="ревьюер не отменяет спецификацию",
    )
    rows = store.training_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["instruction"] == "почини сортировку в отчёте"
    assert row["conflict"]["subject"] == "порядок элементов"
    assert row["conflict"]["higher"]["level"] == "task_contract"
    assert row["conflict"]["lower"]["level"] == "advisor"
    assert row["decision"] == "приоритет у контракта; сортировку не вносим"
    assert row["lesson"] == "ревьюер не отменяет спецификацию"
    assert "stop" in row["correct_action"]
