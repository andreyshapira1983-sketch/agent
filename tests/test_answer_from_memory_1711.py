"""A repeated question is answered from memory when its inputs are unchanged (operator, 2026-11-17)."""
from __future__ import annotations

import dataclasses
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.bootstrap import build_agent
from core.answer_format import format_human_response
from core.loop import AgentLoop
from core.loop_memory_read import AgentLoopMemoryRead
from core.replay_freshness import (
    input_digests,
    read_files,
    stale_inputs,
    tools_allow_replay,
    with_replay_note,
)
from core.smart_memory import EpisodeRecord, EpisodicMemoryStore, _compute_quality_score
from tools.find_in_files import FindInFilesTool

ANSWER = "Conclusion: Код склада — 7391.\nFacts:\n- В файле notes.txt стоит «Код склада: 7391».\nUnverified: nothing"
Q = "Прочитай файл notes.txt и назови код склада."


def _episode(tools=("file_read",), labels=("file:notes.txt",), age=timedelta(minutes=5), answer=ANSWER,
             digests=(), eid="ep-file"):
    return EpisodeRecord(
        goal="g", question=Q, outcome="success",
        summary="s", tools_used=tools, source_labels=labels, verified_chunks=3, unverified_chunks=0,
        answer_quality_score=_compute_quality_score(3, 0, 0), usage_eligible=True,
        completion_state="achieved", full_answer=answer, id=eid, input_digests=digests,
        created_at=(datetime.now(timezone.utc) - age).isoformat(),
    )


def _notes(tmp_path: Path, text: str = "Код склада: 7391\n", mtime: float | None = None) -> Path:
    p = tmp_path / "notes.txt"
    p.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _banked(tmp_path: Path, **kw) -> EpisodeRecord:
    """An episode whose digests were taken as at banking, by a run that began after the file was written."""
    ep = _episode(**kw)
    return dataclasses.replace(ep, input_digests=input_digests(ep, tmp_path, time.time() + 1))


# 1 -- which tools may stand behind a replayed answer
@pytest.mark.parametrize("tools,labels,ok", [
    ((), (), True),
    (("file_read",), ("file:notes.txt",), True),
    (("file_read",), (), False),                       # no named file: nothing to check
    (("file_write",), ("file_write:a.txt",), False),   # an action is redone, never replayed
    (("journal_append",), ("journal_append:data/x.jsonl",), False),
    (("shell_exec",), ("shell_exec:ls",), False),
    (("find_in_files",), ("find_in_files:x",), False),
    (("web_search", "web_fetch", "web_fetch"), ("web:x", "web_fetch:https://python.org"), True),
])
def test_tool_rule(tools, labels, ok):
    assert tools_allow_replay(_episode(tools=tools, labels=labels)) is ok


def test_web_answer_older_than_a_day_is_not_replayed():
    ep = _episode(tools=("web_search", "web_fetch"), labels=("web:x",), age=timedelta(hours=30))
    assert tools_allow_replay(ep) is False


def test_episode_without_a_date_is_not_replayed_on_web_tools():
    ep = dataclasses.replace(_episode(tools=("web_fetch",), labels=("web_fetch:https://a",)), created_at="")
    assert tools_allow_replay(ep) is False


def test_the_gate_uses_the_tool_rule():
    assert AgentLoop._fast_path_allows_replay(_episode(), 0.99) is True
    assert AgentLoop._fast_path_allows_replay(_episode(tools=("file_write",), labels=("file_write:a",)), 0.99) is False


# 2 -- every file behind the answer must hash the same as when the answer was stored
def test_unchanged_file_is_fresh(tmp_path):
    _notes(tmp_path)
    assert stale_inputs(_banked(tmp_path), tmp_path) == []


def test_changed_content_is_stale(tmp_path):
    _notes(tmp_path)
    ep = _banked(tmp_path)
    _notes(tmp_path, "Код склада: 1111\n")
    assert stale_inputs(ep, tmp_path) == ["notes.txt"]


def test_changed_content_with_an_old_mtime_is_stale(tmp_path):
    _notes(tmp_path, mtime=time.time() - 3600)
    ep = _banked(tmp_path)
    _notes(tmp_path, "Код склада: 1111\n", mtime=time.time() - 7200)  # cp -p / git checkout
    assert stale_inputs(ep, tmp_path) == ["notes.txt"]


def test_file_edited_during_the_run_gets_no_digest(tmp_path):
    _notes(tmp_path)
    ep = _episode()
    assert input_digests(ep, tmp_path, time.time() - 60) == ()
    assert stale_inputs(ep, tmp_path) == ["notes.txt"]


def test_missing_file_is_stale(tmp_path):
    _notes(tmp_path)
    ep = _banked(tmp_path)
    (tmp_path / "notes.txt").unlink()
    assert stale_inputs(ep, tmp_path) == ["notes.txt"]


def test_path_outside_the_workspace_is_stale(tmp_path):
    ep = _episode(labels=("file:../../etc/passwd",))
    assert input_digests(ep, tmp_path, time.time()) == ()
    assert stale_inputs(ep, tmp_path) == ["../../etc/passwd"]


def test_a_file_the_loop_read_itself_is_checked(tmp_path):
    """No file_read in tools_used, yet a `file:` label: the loop read it itself (loop_synthesis)."""
    ep = _episode(tools=(), labels=("file:notes.txt",))
    assert read_files(ep) == ["notes.txt"]
    assert stale_inputs(ep, tmp_path) == ["notes.txt"]


def test_a_windowed_read_label_names_the_file():
    assert read_files(_episode(labels=("file:notes.txt:1-5",))) == ["notes.txt"]


def test_digests_survive_the_store(tmp_path):
    _notes(tmp_path)
    store = EpisodicMemoryStore(tmp_path / "data" / "episodic_memory.jsonl")
    store.save(_banked(tmp_path))
    assert stale_inputs(store.load()[0], tmp_path) == []


# 3 -- end to end through candidate choice, the gate and the note, as in `_run_inner`
class _Goal:
    description = "g"


def _served(tmp_path, episode, seeded=()):
    agent = build_agent(tmp_path, with_memory=True, approval_provider=None)
    for ep in seeded:
        agent.episodic_store.save(ep)
    agent._last_best_similar_episode, agent._last_best_similar_score = \
        AgentLoopMemoryRead._fast_path_candidate(agent, Q, episode, 0.99, 0.40)
    out = agent._episodic_fast_path(Q, file_hint=None, goal=_Goal(), local_critique_active=False)
    return None if out is None else agent._noted_replay(out)


def test_fast_path_serves_the_stored_answer_with_a_note(tmp_path):
    _notes(tmp_path)
    out = _served(tmp_path, _banked(tmp_path))
    assert out is not None
    shown = format_human_response(out)
    assert "7391" in shown and "из памяти" in shown


def test_fast_path_redoes_the_work_when_the_file_changed(tmp_path):
    _notes(tmp_path)
    ep = _banked(tmp_path)
    _notes(tmp_path, "Код склада: 1111\n")
    assert _served(tmp_path, ep) is None


def test_fast_path_redoes_an_action(tmp_path):
    ep = _episode(tools=("file_write",), labels=("file_write:notes.txt",))
    assert _served(tmp_path, ep) is None


def test_an_action_is_not_skipped_by_an_older_answer(tmp_path):
    """Latest same ask wrote a file; an older tool-free answer must not stand in for the write."""
    older = _episode(tools=(), labels=(), eid="ep-old", age=timedelta(hours=2))
    latest = _episode(tools=("file_write",), labels=("file_write:notes.txt",), eid="ep-new")
    assert _served(tmp_path, latest, seeded=(older,)) is None


def test_a_latest_search_does_not_hide_an_older_good_answer(tmp_path):
    """A search is a read, not an action: the older verified answer may still be served."""
    _notes(tmp_path)
    older = _banked(tmp_path, eid="ep-old", age=timedelta(hours=1))
    latest = _episode(tools=("list_dir", "find_in_files"), labels=("list_dir:.",), eid="ep-new")
    assert _served(tmp_path, latest, seeded=(older,)) is not None


# 4 -- the note
def test_note_lands_inside_the_conclusion():
    noted = with_replay_note(ANSWER, "из памяти")
    assert "из памяти" in format_human_response(noted)
    assert with_replay_note("plain answer", "из памяти").endswith("из памяти")


def test_note_goes_after_the_whole_multi_paragraph_conclusion():
    answer = "**Conclusion:** A\n\nB para\nFacts:\n- f"
    lines = with_replay_note(answer, "NOTE").splitlines()
    assert lines.index("NOTE") == lines.index("B para") + 1


# 5 -- a search hit shows the matched words, not only the head of a long line
def test_long_line_hit_shows_the_match(tmp_path):
    (tmp_path / "data").mkdir()
    row = '{"_integrity": {"hash": "' + "a" * 260 + '"}, "payload": {"fact": "favorite_color", "value": "green"}}'
    (tmp_path / "data" / "prefs.jsonl").write_text(row + "\n", encoding="utf-8")
    out = FindInFilesTool(tmp_path).run(query="green", path="data")
    assert '"value": "green"' in out
