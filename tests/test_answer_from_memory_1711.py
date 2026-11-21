"""A repeated question is answered from memory when its inputs are unchanged (operator, 2026-11-17)."""
from __future__ import annotations

import dataclasses
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.bootstrap import build_agent
from core.answer_format import format_human_response
from core.loop_memory_read import AgentLoopMemoryRead
from core.replay_freshness import (
    input_digests,
    stale_inputs,
    tools_allow_replay,
)
from core.smart_memory import EpisodeRecord, _compute_quality_score
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


def test_web_answer_older_than_a_day_is_not_replayed():
    ep = _episode(tools=("web_search", "web_fetch"), labels=("web:x",), age=timedelta(hours=30))
    assert tools_allow_replay(ep) is False


def test_episode_without_a_date_is_not_replayed_on_web_tools():
    ep = dataclasses.replace(_episode(tools=("web_fetch",), labels=("web_fetch:https://a",)), created_at="")
    assert tools_allow_replay(ep) is False


def test_missing_file_is_stale(tmp_path):
    _notes(tmp_path)
    ep = _banked(tmp_path)
    (tmp_path / "notes.txt").unlink()
    assert stale_inputs(ep, tmp_path) == ["notes.txt"]


def test_path_outside_the_workspace_is_stale(tmp_path):
    ep = _episode(labels=("file:../../etc/passwd",))
    assert input_digests(ep, tmp_path, time.time()) == ()
    assert stale_inputs(ep, tmp_path) == ["../../etc/passwd"]


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


def test_a_latest_search_does_not_hide_an_older_good_answer(tmp_path):
    """A search is a read, not an action: the older verified answer may still be served."""
    _notes(tmp_path)
    older = _banked(tmp_path, eid="ep-old", age=timedelta(hours=1))
    latest = _episode(tools=("list_dir", "find_in_files"), labels=("list_dir:.",), eid="ep-new")
    assert _served(tmp_path, latest, seeded=(older,)) is not None


# 5 -- a search hit shows the matched words, not only the head of a long line
def test_long_line_hit_shows_the_match(tmp_path):
    (tmp_path / "data").mkdir()
    row = '{"_integrity": {"hash": "' + "a" * 260 + '"}, "payload": {"fact": "favorite_color", "value": "green"}}'
    (tmp_path / "data" / "prefs.jsonl").write_text(row + "\n", encoding="utf-8")
    out = FindInFilesTool(tmp_path).run(query="green", path="data")
    assert '"value": "green"' in out
