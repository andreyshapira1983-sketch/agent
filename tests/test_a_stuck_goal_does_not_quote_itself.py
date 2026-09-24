"""Цель «Ты застрял» не цитирует сама себя (замер 2026-09-24).

Кампания показывала «Ты застрял: цель «Ты застрял: цель «Почини свой дефект…»»
впустую…»: доказательство застревания бралось из ВСЕХ пустых целей, в том
числе из самой цели «спроси», и каждый её провал вкладывал её текст в
следующую — матрёшка росла с каждым кругом, а настоящий предмет уходил за
обрезку в 120 символов.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.stuck_route import stuck_evidence, stuck_goal


def _ledger(root: Path, goals: list[str]) -> None:
    (root / "data").mkdir(parents=True, exist_ok=True)
    rows = [{"goal": g, "result": "empty"} for g in goals]
    (root / "data" / "campaign_ledger.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def test_a_failed_ask_is_counted_not_quoted(tmp_path: Path) -> None:
    first = stuck_goal_text(tmp_path, ["Почини свой дефект X"] * 2)
    _ledger(tmp_path, ["Почини свой дефект X"] * 2 + [first] * 2)
    goal = stuck_goal(tmp_path).goal
    assert goal.count("Ты застрял") == 1, goal
    assert "Почини свой дефект X" in goal
    assert "сама прошла впустую 2 раз" in goal


def test_the_real_subject_is_kept_after_many_rounds(tmp_path: Path) -> None:
    goals = ["Почини свой дефект X"] * 2
    for _ in range(3):
        _ledger(tmp_path, goals)
        goals = goals + [stuck_goal(tmp_path).goal] * 2
    _ledger(tmp_path, goals)
    evidence = stuck_evidence(tmp_path)
    assert all("Ты застрял" not in e for e in evidence), evidence
    assert any("Почини свой дефект X" in e for e in evidence), evidence


def stuck_goal_text(root: Path, goals: list[str]) -> str:
    _ledger(root, goals)
    return stuck_goal(root).goal
