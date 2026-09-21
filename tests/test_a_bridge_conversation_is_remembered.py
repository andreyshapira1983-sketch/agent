"""Разговор через мостик помнится: `--history` кладёт прошлые реплики в память сессии.

Оператор, 2026-09-20 и снова 2026-09-21: «между вами не диалог, а команды».
Мостик зовёт `main.py --ask` новым процессом на каждую реплику, и агент видел
каждое сообщение с чистого листа — не помнил, что сам нашёл час назад, не
слышал поправок, а пятые ворота честно отвечали «в этой сессии ещё нет
предыдущего шага». Замечено 20.09 в 18:09 и брошено.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from cli import one_shot
from core.memory import WorkingMemory


def test_history_file_is_read_as_question_answer_pairs(tmp_path: Path) -> None:
    f = tmp_path / "h.jsonl"
    rows = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(12)]
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    got = one_shot.read_history(str(f))
    assert len(got) == one_shot.HISTORY_TURNS
    assert got[-1] == ("q11", "a11")
    assert one_shot.read_history(None) == []
    assert one_shot.read_history(str(tmp_path / "missing.jsonl")) == []


def test_the_session_memory_holds_the_conversation(monkeypatch, tmp_path: Path) -> None:
    built: dict = {}

    def fake_build(workspace, **kw):
        built.update(kw)
        agent = SimpleNamespace(memory=WorkingMemory() if kw["with_memory"] else None)
        built["agent"] = agent
        return agent

    monkeypatch.setattr(one_shot.budget_guard, "_run_agent_with_budget_guard",
                        lambda agent, **kw: "Conclusion: ok")
    monkeypatch.setattr(one_shot.intent_bridge, "_handle_local_operator_reply", lambda *a: False)
    monkeypatch.setattr(one_shot.intent_bridge, "handle_conversational_operator_input",
                        lambda *a: False)
    one_shot.run_one_shot("а что ты нашёл в строке 186?", workspace=tmp_path,
                          build_agent=fake_build,
                          history=[("найди дефект подстановки", "строка 186 вклеивает весь вывод")])
    assert built["with_memory"] is True and built["with_persistent"] is False
    turns = built["agent"].memory.turns
    assert [t.answer for t in turns] == ["строка 186 вклеивает весь вывод"]


def test_without_history_the_one_shot_stays_memory_free(monkeypatch, tmp_path: Path) -> None:
    built: dict = {}

    def fake_build(workspace, **kw):
        built.update(kw)
        return SimpleNamespace(memory=None)

    monkeypatch.setattr(one_shot.budget_guard, "_run_agent_with_budget_guard",
                        lambda agent, **kw: "Conclusion: ok")
    monkeypatch.setattr(one_shot.intent_bridge, "_handle_local_operator_reply", lambda *a: False)
    monkeypatch.setattr(one_shot.intent_bridge, "handle_conversational_operator_input",
                        lambda *a: False)
    one_shot.run_one_shot("вопрос", workspace=tmp_path, build_agent=fake_build)
    assert built["with_memory"] is False
