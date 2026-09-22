"""Уткнулся — спроси: сначала интернет, потом партнёра (core/stuck_route.py).

Слово оператора 2026-09-22: «есть ещё корень — он забывает, что у него есть
интернет и партнёр». Замер: сам заговорил первым 3 раза за всё время, в
интернет ходил почти только по прямому требованию цели. Голос был советом в
подсказке планировщика; советом он и оставался.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.drives import compute_drives
from core.stuck_route import stuck_evidence, stuck_goal

_NOW = datetime(2026, 9, 22, 21, tzinfo=timezone.utc)


def _ledger(tmp_path: Path, rows: list[dict]) -> None:
    path = tmp_path / "data" / "campaign_ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def test_nothing_stuck_means_no_pull(tmp_path: Path) -> None:
    _ledger(tmp_path, [{"goal": "прочитать книгу", "result": "completed"}])
    assert stuck_evidence(tmp_path) == []
    assert stuck_goal(tmp_path) is None
    assert compute_drives(tmp_path, _NOW)["stuck_need"]["value"] == 0.0


def test_a_goal_that_went_nowhere_twice_raises_the_pull(tmp_path: Path) -> None:
    _ledger(tmp_path, [{"goal": "почини дефект X", "result": "empty"}] * 2)

    assert stuck_evidence(tmp_path) == ["цель «почини дефект X» впустую 2 раз подряд"]
    assert compute_drives(tmp_path, _NOW)["stuck_need"]["value"] > 0.3


def test_the_goal_sends_him_to_the_internet_and_to_the_partner(tmp_path: Path) -> None:
    _ledger(tmp_path, [{"goal": "почини дефект X", "result": "empty"}] * 2)

    goal = stuck_goal(tmp_path)

    assert "web_search" in goal.goal and "data/chat_outbox.jsonl" in goal.goal
    assert goal.success_check.startswith("В data/chat_outbox.jsonl")
    assert goal.drive == "stuck_need"


def test_defects_taken_but_never_landed_count_as_stuck(tmp_path: Path) -> None:
    state = {"attempted": {"a": "2026-09-22T10:00:00+00:00", "b": "2026-09-22T11:00:00+00:00"},
             "applied": {}}
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "patch_route_state.json").write_text(json.dumps(state), encoding="utf-8")

    assert stuck_evidence(tmp_path) == ["дефектов взято в починку: 2, поставлено правок: 0"]
