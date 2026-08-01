"""Tests for core.conflict_episode (procedural memory of instruction conflicts).

The properties worth pinning: an episode is banked *before* anyone rules on it,
a ruling never overwrites history, and an unresolved episode can't leak into a
training set as though it had an answer.
"""
from __future__ import annotations

from pathlib import Path

from core.conflict_episode import (
    STATUS_OPEN,
    STATUS_RESOLVED,
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


def test_blocked_outcome_produces_one_episode_per_subject():
    outcome = evaluate(extract_from_task_and_review(
        task_text="Порядок стабильный. Сетевые вызовы запрещены.",
        review_text="Отсортируй по имени. Возьми данные через API.",
    ))
    episodes = episodes_from_outcome(outcome, context="задача")
    assert {e.subject for e in episodes} == {"порядок элементов", "сетевые вызовы"}


def test_new_episode_is_open_and_unruled():
    episode = _episode()
    assert episode.status == STATUS_OPEN
    assert episode.is_open
    assert episode.ruling == ""
    assert episode.ruled_by == ""


def test_episode_keeps_both_sources_and_the_verdict():
    episode = _episode()
    assert episode.higher["source_level"] == "task_contract"
    assert episode.lower["source_level"] == "advisor"
    assert "выше по полномочиям" in episode.priority_verdict
    assert episode.context == "почини сортировку в отчёте"


def test_episode_records_what_was_blocked():
    episode = _episode()
    assert "git_commit" in episode.blocked_actions
    assert "modify_test" in episode.blocked_actions


def test_episode_ids_are_unique():
    first, second = _episode(), _episode()
    assert first.id != second.id


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def test_resolve_returns_a_new_object_and_leaves_the_original_open():
    episode = _episode()
    resolved = episode.resolve(
        ruling="приоритет у контракта, сортировку не вносим",
        ruled_by="оператор",
        lesson="ревьюер не отменяет спецификацию",
    )
    assert resolved.status == STATUS_RESOLVED
    assert resolved.id == episode.id
    assert episode.status == STATUS_OPEN, "the original must not be mutated"


def test_resolve_stamps_a_time():
    resolved = _episode().resolve(ruling="контракт", ruled_by="оператор")
    assert resolved.ruled_at


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def test_default_path_is_under_the_workspace_data_dir(tmp_path: Path):
    assert default_path(tmp_path) == tmp_path / "data" / "conflict_episodes.jsonl"


def test_empty_store_reads_empty(tmp_path: Path):
    store = ConflictEpisodeStore(default_path(tmp_path))
    assert store.load_all() == ()
    assert store.load_open() == ()
    assert store.load_recent() == ()


def test_save_and_read_back(tmp_path: Path):
    store = ConflictEpisodeStore(default_path(tmp_path))
    episode = _episode()
    store.save(episode)
    loaded = store.load_all()
    assert len(loaded) == 1
    assert loaded[0].id == episode.id
    assert loaded[0].to_dict() == episode.to_dict()


def test_save_many_counts_rows(tmp_path: Path):
    store = ConflictEpisodeStore(default_path(tmp_path))
    assert store.save_many([_episode(), _episode()]) == 2
    assert store.save_many([]) == 0
    assert len(store.load_all()) == 2


def test_resolution_supersedes_without_rewriting_history(tmp_path: Path):
    path = default_path(tmp_path)
    store = ConflictEpisodeStore(path)
    episode = _episode()
    store.save(episode)

    resolved = store.resolve(
        episode.id, ruling="контракт выше", ruled_by="оператор", lesson="урок",
    )
    assert resolved is not None
    assert resolved.status == STATUS_RESOLVED

    # one logical episode…
    assert len(store.load_all()) == 1
    assert store.load_all()[0].status == STATUS_RESOLVED
    # …but both rows are still on disk
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_resolve_reads_and_appends_inside_one_lock(tmp_path: Path, monkeypatch):
    """Two operators resolving the same episode must not lose a ruling.

    Reading outside the lock lets both see the ``open`` row and both append;
    reads collapse by id keeping the last, so one ruling would vanish.
    """
    import contextlib

    import core.conflict_episode as mod

    store = ConflictEpisodeStore(default_path(tmp_path))
    episode = _episode()
    store.save(episode)

    events: list[str] = []
    real_lock = mod.state_file_lock
    real_read = mod.read_state_jsonl_unlocked
    real_append = mod.append_state_jsonl_unlocked

    @contextlib.contextmanager
    def tracking_lock(path):
        assert "enter" not in events, "a nested lock acquisition would deadlock"
        events.append("enter")
        with real_lock(path):
            yield
        events.append("exit")

    monkeypatch.setattr(mod, "state_file_lock", tracking_lock)
    monkeypatch.setattr(
        mod, "read_state_jsonl_unlocked",
        lambda path: (events.append("read"), real_read(path))[1],
    )
    monkeypatch.setattr(
        mod, "append_state_jsonl_unlocked",
        lambda path, rows: (events.append("append"), real_append(path, rows))[1],
    )

    store.resolve(episode.id, ruling="контракт выше", ruled_by="оператор")

    assert events == ["enter", "read", "append", "exit"]


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


def test_corrupt_row_does_not_hide_the_others(tmp_path: Path):
    path = default_path(tmp_path)
    store = ConflictEpisodeStore(path)
    store.save(_episode())
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{ not json at all\n")
    assert len(store.load_all()) == 1


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


def test_round_trip_through_dict_is_lossless():
    episode = _episode().resolve(
        ruling="контракт", ruled_by="оператор", lesson="урок",
    )
    assert ConflictEpisode.from_dict(episode.to_dict()).to_dict() == (
        episode.to_dict()
    )
