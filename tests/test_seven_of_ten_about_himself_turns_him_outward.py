"""Семь из десяти последних целей про себя — следующая обязана смотреть наружу.

Слово оператора 2026-09-25 (число 7 из 10 — его решение). Замер того же дня:
все 52 цели, выбранные агентом через хартию, были про себя; 23–24.09 ни одной
внешней цели в журнале кампаний.
"""
from __future__ import annotations

import json

import pytest

from core.charter_goal import DECISIONS_RELPATH, goal_faces, propose_charter_goal
from tests.test_the_charter_names_the_next_goal import _LLM, _reply, _workspace

_SELF_GOALS = [f"Read core/module_{i}.py and record which block of it can be extracted, with file:line"
               for i in range(10)]
_WORLD = ("Найти в интернете первоисточник — RFC 9110 на rfc-editor.org — и выписать точную "
          "формулировку идемпотентности методов")


def _chosen(ws, goals) -> None:
    path = ws / DECISIONS_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"status": "proposed", "goal": g}, ensure_ascii=False) + "\n"
                            for g in goals), encoding="utf-8")


@pytest.mark.parametrize(("goal", "faces"), [
    (_WORLD, "world"),
    ("В книге knowledge_library/cs/txt/Morin.txt найти раздел про кучи", "world"),
    ("Take one market order and deliver it", "world"),
    ("Read the run log where obligation_silently_missing fired and record why", "self"),
    ("Почини свой дефект в core/step_sanitizer.py", "self"),
])
def test_a_goal_faces_the_world_only_when_it_names_an_outside_source(goal: str, faces: str) -> None:
    assert goal_faces(goal) == faces


def test_after_seven_of_ten_a_goal_about_himself_is_declined(tmp_path) -> None:
    ws = _workspace(tmp_path)
    _chosen(ws, _SELF_GOALS[:7] + [_WORLD + f" {i}" for i in range(3)])
    llm = _LLM(_reply(goal="Read core/planner.py and record one cohesive block to extract, with file:line"))
    result = propose_charter_goal(llm, ws)
    assert result.status == "declined" and "7 of your last 10" in result.reason
    assert "MUST FACE THE OUTSIDE WORLD" in llm.user, "the model is told before it spends a call"


def test_after_seven_of_ten_a_goal_facing_the_world_passes(tmp_path) -> None:
    ws = _workspace(tmp_path)
    _chosen(ws, _SELF_GOALS)
    result = propose_charter_goal(_LLM(_reply(goal=_WORLD)), ws)
    assert "about yourself" not in result.reason, result.reason


def test_six_of_ten_leaves_the_choice_free(tmp_path) -> None:
    ws = _workspace(tmp_path)
    _chosen(ws, _SELF_GOALS[:6] + [_WORLD + f" {i}" for i in range(4)])
    llm = _LLM(_reply(goal="Read core/planner.py and record one cohesive block to extract, with file:line"))
    result = propose_charter_goal(llm, ws)
    assert "about yourself" not in result.reason and "MUST FACE" not in llm.user
