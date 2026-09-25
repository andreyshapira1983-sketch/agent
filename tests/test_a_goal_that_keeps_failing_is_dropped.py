"""Цель, которой четыре платных захода не дали ничего, бросается до перемены мира.

Voyager (arXiv 2305.16291): после четырёх неудачных раундов задача
оставляется. Замер 2026-09-25: одна цель прокрутилась 34 раза вхолостую, 16
целей — 4+ раз; исход «пусто» по замыслу не занимал тему, и предела не было.
"""
from __future__ import annotations

import json

from core.charter_goal import propose_charter_goal
from tests.test_the_charter_names_the_next_goal import _LLM, _reply, _workspace

_GOAL = ("Свести повтор в модуле core/secret_scanner.py: дубль определений шаблонов "
         "ключей, одна таблица вместо двух")


def _ledger(ws, rows) -> None:
    path = ws / "data" / "campaign_ledger.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _miss(ts: str, calls: int = 6) -> dict:
    return {"goal": _GOAL, "ts": ts, "result": "empty", "work_done": False, "llm_calls_spent": calls}


def test_four_paid_misses_drop_the_goal(tmp_path) -> None:
    ws = _workspace(tmp_path)
    _ledger(ws, [_miss(f"2026-09-22T12:0{i}:00+00:00") for i in range(4)])
    result = propose_charter_goal(_LLM(_reply(goal=_GOAL)), ws)
    assert result.status == "declined" and "abandoned after 4+" in result.reason


def test_three_misses_and_free_waits_do_not(tmp_path) -> None:
    ws = _workspace(tmp_path)
    _ledger(ws, [_miss(f"2026-09-22T12:0{i}:00+00:00") for i in range(3)]
            + [_miss(f"2026-09-22T13:0{i}:00+00:00", calls=0) for i in range(5)])
    result = propose_charter_goal(_LLM(_reply(goal=_GOAL)), ws)
    assert result.status == "proposed", result.reason


def test_a_changed_world_reopens_the_goal(tmp_path) -> None:
    ws = _workspace(tmp_path)
    _ledger(ws, [_miss(f"2026-09-22T12:0{i}:00+00:00") for i in range(4)])
    events = ws / "data" / "capability_events.jsonl"
    events.write_text("".join(json.dumps(r) + "\n" for r in (
        {"ts": "2026-09-20T00:00:00+00:00", "blocked_tools": ["a"]},
        {"ts": "2026-09-23T00:00:00+00:00", "blocked_tools": ["a", "b"]})), encoding="utf-8")
    result = propose_charter_goal(_LLM(_reply(goal=_GOAL)), ws)
    assert "abandoned" not in result.reason, result.reason
