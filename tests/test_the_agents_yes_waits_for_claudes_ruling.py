"""«Цель достигнута» судит Клод вне хода; до его решения — не зачёт (слово оператора 25.09).

Судья той же семьи моделей завышает своё (arXiv 2404.13076); из 10 «verified»
целей ложных было 5. См. core/judge_queue.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.campaign_verdict import record_campaign_verdict
from core.judge_queue import agreement, pending, rule, standing, submit
from core.work_usefulness import _verified_goals
from scripts.judge_queue import main as judge_cli


def _verdict(goal: str, word: str = "verified") -> dict:
    return {"goal": goal, "verdict": word, "success_check": "file data/notes/x.md exists",
            "reason": "fresh", "fresh_traces": ["data/notes/x.md"],
            "ts": datetime.now(timezone.utc).isoformat()}


def test_the_same_decision_is_queued_once(tmp_path: Path) -> None:
    a = submit(tmp_path, kind="goal_verified", subject="g", provisional="verified", evidence=["x"])
    b = submit(tmp_path, kind="goal_verified", subject="g", provisional="verified", evidence=["x"])
    assert a == b and len(pending(tmp_path)) == 1


def test_a_ruling_needs_a_reason_a_known_item_and_happens_once(tmp_path: Path) -> None:
    item = submit(tmp_path, kind="goal_verified", subject="g", provisional="verified")
    with pytest.raises(ValueError):
        rule(tmp_path, item, "confirmed", "  ")
    with pytest.raises(KeyError):
        rule(tmp_path, "jq_nope", "confirmed", "opened it")
    assert standing(tmp_path, item) == "provisional"
    rule(tmp_path, item, "overturned", "the note is an empty skeleton")
    assert standing(tmp_path, item) == "overturned" and pending(tmp_path) == []
    with pytest.raises(ValueError):
        rule(tmp_path, item, "confirmed", "changed my mind")
    assert agreement(tmp_path)["goal_verified"]["overturned"] == 1


def test_only_a_goal_claude_confirmed_counts_as_useful(tmp_path: Path) -> None:
    record_campaign_verdict(tmp_path, _verdict("kept"))
    record_campaign_verdict(tmp_path, _verdict("thrown"))
    assert _verified_goals(tmp_path / "data") == []  # предварительные — не зачёт
    by_subject = {i["subject"]: i["id"] for i in pending(tmp_path)}
    rule(tmp_path, by_subject["kept"], "confirmed", "file has the requested sections")
    rule(tmp_path, by_subject["thrown"], "overturned", "file is empty")
    assert [g for _, g in _verified_goals(tmp_path / "data")] == ["kept"]


def test_claudes_command_lists_and_rules(tmp_path: Path, capsys) -> None:
    item = submit(tmp_path, kind="goal_verified", subject="g", provisional="verified")
    assert judge_cli(["--root", str(tmp_path), "list"]) == 0
    assert item in capsys.readouterr().out
    assert judge_cli(["--root", str(tmp_path), "rule", item, "confirmed", "opened x.md: has it"]) == 0
    assert standing(tmp_path, item) == "confirmed"
