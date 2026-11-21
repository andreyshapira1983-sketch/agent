"""Требования человека проверяются чек-листом «да/нет», а не только файлом и тестами.

Замер 2026-09-23 (чат, #538): поручение требовало в конце короткий ответ
оператору не длиннее 8 предложений и метку FACT/INFERENCE у каждого вывода;
ответ не дал ни того, ни другого, а ход считался выполненным — договор
выполненности проверял только «файл создан / изменён / тесты зелёные».

Как по литературе (TICK, arXiv 2410.03608; RLCF, arXiv 2507.18624): чек-лист
составляется из поручения ДО работы, каждый пункт — вопрос «да/нет» с весом
важности; точное (подстрока есть/нет) проверяет программа, остальное — судья.
Сторож односторонний: важное «нет» понижает «выполнено», ничего не повышает;
сбой модели — «не знаю», не обвинение.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from core.request_checklist import (
    BUILD_SYSTEM,
    ENV,
    JUDGE_SYSTEM,
    Checklist,
    ChecklistItem,
    build_checklist,
    check_answer,
)
from core.smart_memory import EpisodicMemoryStore
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry

_REQUEST = "Коротко объясни, что такое BM25. В конце строка VERDICT: и не больше трёх предложений."
_CHECKLIST = json.dumps({"items": [
    {"question": "Не больше трёх предложений?", "quote": "не больше трёх  предложений",
     "importance": 80, "rule": None, "arg": None},
    {"question": "Есть строка VERDICT:?", "quote": "В конце строка VERDICT:",
     "importance": 90, "rule": "must_include", "arg": "VERDICT:"},
    {"question": "Объяснено ли, что такое BM25?", "quote": "что такое BM25",
     "importance": 20, "rule": None, "arg": None},
    # Выдуманное требование: в поручении этих слов нет — пункт отбрасывается.
    {"question": "Извинился ли агент?", "quote": "извинись за прошлый ответ",
     "importance": 95, "rule": None, "arg": None},
]}, ensure_ascii=False)


class _Llm(FakeLLM):
    """Чек-лист и судья отвечают по своей системной подсказке, остальное — по очереди."""

    def __init__(self, judge: list[str], answer: str, *, fail_judge: bool = False):
        super().__init__(responses=[answer])
        self.judge, self.fail_judge = judge, fail_judge

    def complete(self, system, user, **kwargs):
        if system == BUILD_SYSTEM:
            self.calls.append({"system": system, "user": user})
            return _CHECKLIST
        if system == JUDGE_SYSTEM:
            self.calls.append({"system": system, "user": user})
            if self.fail_judge:
                raise RuntimeError("provider down")
            numbers = [int(n) for n in re.findall(r"^(\d+)\. ", user.split("QUESTIONS:")[1], re.MULTILINE)]
            return json.dumps({"answers": [{"n": n, "answer": a, "why": "t"}
                                           for n, a in zip(numbers, self.judge, strict=False)]})
        body = super().complete(system, user, **kwargs)
        nonce = re.search(r"\[\[agent\.completion:([a-f0-9]+):<token>\]\]", system)
        return f"{body}\n[[agent.completion:{nonce[1]}:achieved]]" if nonce else body


def _run(tmp_path: Path, llm: FakeLLM):
    registry = ToolRegistry()
    agent = AgentLoop(
        registry=registry, policy=PolicyGate(registry), llm=llm,
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=tmp_path, verbose=False),
        planner=FakePlanner(sources=[], reasoning="Answer from knowledge."),
        episodic_store=EpisodicMemoryStore(tmp_path / "episodes.jsonl"),
        max_replan_attempts=1,
    )
    agent.run(_REQUEST)
    events = [json.loads(x) for x in Path(agent.log.path).read_text(encoding="utf-8").splitlines()]
    return agent, events


def _names(events):
    return [e["event"] for e in events]


_ANSWER = "BM25 ранжирует документы по частоте слов с поправкой на длину. VERDICT: понятно."


def test_an_unmet_important_item_lowers_achieved(tmp_path, monkeypatch) -> None:
    """ГЛАВНОЕ, сквозь настоящий ход: «нет» на важный пункт — «выполнено частично»."""
    monkeypatch.setenv(ENV, "1")
    agent, events = _run(tmp_path, _Llm(["NO", "YES", "YES"], _ANSWER))

    names = _names(events)
    assert "request_checklist" in names and "checklist_verdict" in names
    assert names.index("request_checklist") < names.index("checklist_verdict")
    episode = agent.episodic_store.load()[-1]
    assert episode.declared_completion == "achieved"
    assert episode.completion_state == "partially_achieved"
    assert episode.completion_override == "checklist_unmet"


def test_a_failed_judge_is_not_an_accusation(tmp_path, monkeypatch) -> None:
    """Судья упал — «не знаю»; программный пункт (VERDICT: есть) остался «да»."""
    monkeypatch.setenv(ENV, "1")
    agent, events = _run(tmp_path, _Llm([], _ANSWER, fail_judge=True))
    verdict = next(e["payload"] for e in events if e["event"] == "checklist_verdict")
    assert verdict["unmet_important"] == []
    assert agent.episodic_store.load()[-1].completion_state == "achieved"


@pytest.mark.parametrize("raw", ["", "не json", '{"items": []}', '{"items": [{"question": ""}]}'])
def test_an_unusable_checklist_is_empty_not_invented(raw: str) -> None:
    class _Raw(FakeLLM):
        def complete(self, system, user, **kwargs):
            return raw
    assert build_checklist(_Raw(), _REQUEST).items == ()


def test_a_no_the_judge_does_not_repeat_is_not_counted() -> None:
    """Замер 24.09 (#542): тот же ответ судья признавал то выполненным, то нет.
    Важное «нет» спрашивается второй раз; не повторилось — «не знаю»."""
    class _Flip(_Llm):
        def __init__(self):
            super().__init__([], "")
            self.rounds = [["NO"], ["YES"]]

        def complete(self, system, user, **kwargs):
            if system == JUDGE_SYSTEM:
                self.judge = self.rounds.pop(0)
            return super().complete(system, user, **kwargs)

    checklist = Checklist(items=(ChecklistItem("Не больше трёх предложений?", 80),), reason="t")
    llm = _Flip()
    verdict = check_answer(llm, _REQUEST, checklist, "ответ")
    assert verdict.verdicts[0].answer == "unknown" and verdict.unmet == ()
    assert sum(c["system"] == JUDGE_SYSTEM for c in llm.calls) == 2
