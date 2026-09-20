"""Разговор с человеком — опыт, а не мусор.

Слово оператора 2026-09-20, вечер: «чаты, которые он с тобой разговаривает,
столько информации, он просто проёбывает её».

Замер того вечера. Через `--ask` прошло около двадцати пяти обменов. В них
агент установил, что ключ `-I` отбрасывает `PYTHONPATH` и потому его
собственная заявка `sii_f4e85e91ead006cd` предлагает нерабочее лекарство;
что слово `irreversible` он ставил, не зная его цены; что объясняет стену
вместо того, чтобы её проверить; что хранилище определяется читателем, а не
названием. После этого в `data/episodic_memory.jsonl` двести эпизодов и НИ
ОДНОГО про этот разговор.

Причина не в качестве памяти. Её там не было: `cli/one_shot.py` строил
агента с `with_memory=False`, а `app/bootstrap.py` выводил из этого
`with_experience = with_memory`, то есть `episodic_store = None`. Складывать
было некуда.

Тот же изъян уже чинили однажды — для безлюдного пути; комментарий рядом с
`if with_experience:` в bootstrap говорит дословно, что прежняя связка
«оставляла агента неспособным записывать и вспоминать опыт вообще». Канал,
которым с агентом говорит ЧЕЛОВЕК, тогда забыли.

Обещание `--ask` («One-shot question, no memory») остаётся честным: оно про
сессию и про `data/persistent_memory.jsonl`, и обе оси по-прежнему
выключены — это проверяется здесь же, чтобы починка одной оси не съела
соседнюю. Опыт — третья ось, и в замороженном контракте
(`tests/characterization/test_cli_one_shot_policy.py`) о ней нет ни слова.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import app.budget_guard as budget_guard_module
import cli.app as app_module
import cli.intent_bridge as bridge_module
import main as main_module


def _fake_agent() -> SimpleNamespace:
    return SimpleNamespace(log=SimpleNamespace(log=lambda *a, **k: None, trace_id="trace-fake"))


def _build_calls(monkeypatch: pytest.MonkeyPatch, tmp_path) -> list[dict]:
    calls: list[dict] = []

    def fake_build_agent(workspace, **kwargs):
        calls.append({"workspace": workspace, **kwargs})
        return _fake_agent()

    monkeypatch.setattr(app_module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "build_agent", fake_build_agent)
    monkeypatch.setattr(bridge_module, "_handle_local_operator_reply", lambda *a, **k: False)
    monkeypatch.setattr(
        bridge_module, "handle_conversational_operator_input", lambda *a, **k: False)
    monkeypatch.setattr(
        budget_guard_module, "_run_agent_with_budget_guard", lambda *a, **k: "answer")
    monkeypatch.setattr(
        sys, "argv", ["main.py", "--workspace", str(tmp_path), "--ask", "что ты помнишь"])
    return calls


def test_the_one_shot_channel_carries_an_experience_store(monkeypatch, tmp_path) -> None:
    """Опыт включён: разговору есть куда лечь."""
    calls = _build_calls(monkeypatch, tmp_path)
    assert main_module.main() == 0
    assert calls, "агент не был построен"
    assert calls[0].get("with_experience") is True, calls[0]


def test_the_promise_of_a_fresh_session_is_kept(monkeypatch, tmp_path) -> None:
    """Сессионная и постоянная память остаются выключенными — обещание в силе."""
    calls = _build_calls(monkeypatch, tmp_path)
    assert main_module.main() == 0
    assert calls[0]["with_memory"] is False
    assert calls[0]["with_persistent"] is False


def test_memory_prompts_but_does_not_replay_the_answer(monkeypatch, tmp_path) -> None:
    """Прошлый ответ не подменяет новую работу: память подсказывает, не заменяет."""
    calls = _build_calls(monkeypatch, tmp_path)
    assert main_module.main() == 0
    assert calls[0].get("episodic_replay") is False, calls[0]
