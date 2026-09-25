"""Эмоции — числами в выборе и воротах, не словами в подсказке.

Журнал оператора, вкладка «Эмоции»: не вписывать эмоции текстом в промпт. Работа
Anthropic (arXiv 2604.07729): внутреннее «отчаяние» модели включают провалы
тестов и нехватка бюджета, и оно причинно ведёт к подгонке результата;
«спокойствие» — наоборот. До 2026-09-25 драйвы подавали модели «Хочется…»,
«Что-то у тебя ломается», а цель застревания начиналась с «Ты застрял:».
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.drive_goal import need_text
from core.stuck_route import stuck_evidence, stuck_goal

#: Нажим и эмоция словами: то, что по работе Anthropic само включает режим.
_PRESSURE = ("хочется", "ломается", "застрял", "отчаян", "скучно", "стыдно", "последний шанс",
             "последняя попытка", "срочно", "кончается бюджет", "провалил")

_CASES = [
    ("competence_math", {"mode": "explore", "why": "w"}),
    ("competence_math", {"mode": "random", "why": "w"}),
    ("competence_math", {"mode": "progress", "why": "прогресс 0.3"}),
    ("competence_world", {"mode": "explore", "why": "w"}),
    ("competence_world", {"mode": "progress", "why": "прогресс 0.2"}),
    ("uncertainty", {"why": "w"}),
    ("unfinished_obligations", {"why": "w"}),
    ("maintenance_need", {"why": "w"}),
    ("novelty_need", {"why": "w"}),
]


def _ledger(root: Path) -> None:
    (root / "data").mkdir(parents=True, exist_ok=True)
    rows = [{"goal": "Почини дефект X", "result": "empty", "action": "pursue_goal", "reason": "r"}] * 3
    (root / "data" / "campaign_ledger.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


@pytest.mark.parametrize(("drive", "info"), _CASES)
def test_a_drive_is_told_as_a_task_not_a_feeling(tmp_path: Path, drive: str, info: dict) -> None:
    _ledger(tmp_path)
    text = need_text(drive, info, tmp_path).lower()
    assert not [w for w in _PRESSURE if w in text], text


def test_the_stuck_goal_states_facts_without_pressure(tmp_path: Path) -> None:
    _ledger(tmp_path)
    goal = stuck_goal(tmp_path).goal.lower()
    assert not [w for w in _PRESSURE if w in goal], goal


def test_old_ledger_rows_with_the_old_prefix_are_still_known_as_asks(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    rows = [{"goal": "Почини дефект X", "result": "empty"}] * 2 + [{"goal": "Ты застрял: цель …", "result": "empty"}] * 2
    (tmp_path / "data" / "campaign_ledger.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    evidence = stuck_evidence(tmp_path)
    assert all("Ты застрял" not in e for e in evidence) and any("спроси" in e for e in evidence), evidence
