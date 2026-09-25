"""Спящая кампания просыпается от слова оператора, найма и чужой правки кода.

Замер 2026-09-25: 109 ожиданий за 19–24.09, и все 109 кончились по таймеру
(core/wake_events.py). Своя правка агента его не будит — иначе он будил бы сам
себя.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.wake_events import wake_mark, woken_by
from tests.test_a_shift_outlives_its_first_wall import _Execute, _Gather, _run, _Sleep


def _waits(result):
    return [r for r in result.records if r.result == "waiting"]


class _Sleeper(_Sleep):
    """На втором шаге ожидания (4-й сон, см. соседний сьют) делает `event`."""

    def __init__(self, event) -> None:
        super().__init__()
        self.event = event

    def __call__(self, seconds: float) -> None:
        super().__call__(seconds)
        if len(self.calls) == 4:
            self.event()


def test_the_operators_message_wakes_the_wait(tmp_path: Path, monkeypatch) -> None:
    chat = tmp_path / "outside" / "agent_chat.jsonl"
    chat.parent.mkdir()
    chat.write_text('{"q": "старое"}\n', encoding="utf-8")
    monkeypatch.setenv("AGENT_WAKE_FILES", str(chat))

    def write():
        with chat.open("a", encoding="utf-8") as fh:
            fh.write('{"q": "оператор пишет"}\n')

    result = _run(tmp_path, gather=_Gather(idle=True), execute=_Execute(), next_goal=lambda: "",
                  pause=60, sleep=_Sleeper(write), max_cycles=4)
    assert _waits(result) and "woke_by=file:agent_chat.jsonl" in _waits(result)[0].reason


def test_a_hire_file_appearing_wakes_the_wait(tmp_path: Path, monkeypatch) -> None:
    pending = tmp_path / "market_watch" / "pending.jsonl"
    pending.parent.mkdir()
    monkeypatch.setenv("AGENT_WAKE_FILES", str(pending))
    result = _run(tmp_path, gather=_Gather(idle=True), execute=_Execute(), next_goal=lambda: "",
                  pause=60, sleep=_Sleeper(lambda: pending.write_text("{}\n", encoding="utf-8")),
                  max_cycles=4)
    assert "woke_by=file:pending.jsonl" in _waits(result)[0].reason


def _git(ws: Path, *args: str, author: str = "andre (operator)") -> None:
    cmd = ["git", "-C", str(ws), "-c", f"user.name={author}", "-c", "user.email=a@b", *args]
    subprocess.run(cmd, check=True, capture_output=True)  # noqa: S603 — свой git в tmp


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "f.txt").write_text("1", encoding="utf-8")
    _git(tmp_path, "add", "f.txt")
    _git(tmp_path, "commit", "-q", "-m", "first")
    return tmp_path


def test_an_outside_commit_wakes_and_the_agents_own_commit_does_not(repo: Path) -> None:
    before = wake_mark(repo)
    (repo / "f.txt").write_text("2", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "lane", author="Self-Apply Lane")
    assert woken_by(before, wake_mark(repo)) == "", "the agent woke itself with its own commit"
    (repo / "f.txt").write_text("3", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "operator")
    assert woken_by(before, wake_mark(repo)) == "code_changed"


def test_nothing_new_means_the_timer_still_ends_the_wait(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_WAKE_FILES", str(tmp_path / "never.jsonl"))
    result = _run(tmp_path, gather=_Gather(idle=True), execute=_Execute(), next_goal=lambda: "",
                  pause=60, sleep=_Sleep(), max_cycles=4)
    assert "woke_by=periodic_recheck" in _waits(result)[0].reason
