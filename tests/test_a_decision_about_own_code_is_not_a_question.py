"""Решение о своём коде — его; вопрос человеку не засчитывается концом (ночь 26.09).

Живой прогон без вмешательства: агент сам нашёл корень дефекта (детектор
сверяет отчёт со списком инструментов, а не с файлом на диске), спросил
человека «да/нет» и пошёл по кругу «цель не продвигается → пусто → простой».
Успехом цели «уткнулся» был вопрос в data/chat_outbox.jsonl, а этот файл
существует всегда — критерий «файл есть» засчитывал любой исход. Правило «когда
звать человека» жило в двух местах по-разному (core/own_decisions.py).
"""
from __future__ import annotations

import json
from pathlib import Path

from core.own_decisions import DECISIONS_RELPATH, HUMAN_ONLY
from core.planner_prompt import PLANNER_SYSTEM
from core.stuck_route import stuck_goal
from core.success_check import observe_success_check
from tools.journal_append import JournalAppendTool


def _stuck(root: Path) -> None:
    (root / "data").mkdir(parents=True, exist_ok=True)
    rows = [{"goal": "Почини свой дефект X", "result": "empty"}] * 2
    (root / "data" / "campaign_ledger.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _append(root: Path, path: str, record: dict) -> None:
    JournalAppendTool(workspace_root=root).run(path=path, record=record)


def test_asking_the_human_alone_does_not_complete_the_goal(tmp_path: Path) -> None:
    _stuck(tmp_path)
    goal = stuck_goal(tmp_path)
    _append(tmp_path, "data/chat_outbox.jsonl",
            {"author": "agent", "reason": "stuck", "text": "Считать эталоном файл на диске: да или нет?"})

    assert observe_success_check(goal.success_check, tmp_path)["verdict"] == "missing"


def test_only_this_goals_own_decision_completes_it(tmp_path: Path) -> None:
    _stuck(tmp_path)
    goal = stuck_goal(tmp_path)
    rid = goal.success_check.split('"id": "')[1].rstrip('"')
    old = {"id": "reshenie-00000000", "about": "другое", "decision": "старое", "because": "b"}
    _append(tmp_path, DECISIONS_RELPATH, old)
    assert observe_success_check(goal.success_check, tmp_path)["verdict"] == "missing", \
        "a file that already exists must not pass for this goal's decision"

    _append(tmp_path, DECISIONS_RELPATH, {
        "id": rid, "about": "sii_dda321e2221d400d",
        "decision": "эталон — факт записи файла на диске; правлю core/answer_contradiction.py",
        "because": "logs/trace_271045b1: инструмент с отказом попал в executed_tools"})
    assert observe_success_check(goal.success_check, tmp_path)["verdict"] == "verified"


def test_the_next_repair_goal_carries_his_own_decision(tmp_path: Path) -> None:
    from core.patch_route import defect_goal
    from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry
    from core.state_integrity import read_state_jsonl_unlocked, rewrite_state_jsonl_unlocked

    path = tmp_path / DEFAULT_ISSUE_PATH
    SelfImprovementIssueRegistry(path).upsert_failure("детектор отчёта", "2026-09-25T18:00:00+00:00")
    rows = read_state_jsonl_unlocked(path)
    for row in rows:
        row["suggested_next_action"] = "decide whether the defect is in the answer or the detector"
    rewrite_state_jsonl_unlocked(path, rows)
    fingerprint = SelfImprovementIssueRegistry(path).unresolved()[0].fingerprint
    _append(tmp_path, DECISIONS_RELPATH, {
        "id": "reshenie-1", "about": fingerprint,
        "decision": "эталон — факт записи файла на диске", "because": "trace 271045b1"})

    goal = defect_goal(tmp_path).goal

    assert "эталон — факт записи файла на диске" in goal and "Делай правку по нему" in goal


def test_one_rule_for_when_the_human_decides(tmp_path: Path) -> None:
    _stuck(tmp_path)
    goal = stuck_goal(tmp_path).goal
    planner = " ".join(PLANNER_SYSTEM.split())
    for case in HUMAN_ONLY:
        assert case in goal, case
        assert case in planner, case
    assert "застрял после своих попыток" not in planner, "the old second rule is gone"


def test_a_decision_without_its_reason_is_refused(tmp_path: Path) -> None:
    tool = JournalAppendTool(workspace_root=tmp_path)
    try:
        tool.run(path=DECISIONS_RELPATH, record={"id": "r", "about": "x", "decision": "y"})
    except ValueError as exc:
        assert "because" in str(exc)
    else:
        raise AssertionError("a decision without evidence was accepted")
