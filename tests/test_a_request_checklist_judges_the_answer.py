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
    UNIVERSAL,
    UNIVERSAL_IMPORTANCE,
    Checklist,
    ChecklistItem,
    build_checklist,
    check_answer,
)
from core.smart_memory import EpisodicMemoryStore, assemble_completion_verdict
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
            numbers = [int(n) for n in re.findall(r"^(\d+)\. ", user.split("QUESTIONS:")[1], re.M)]
            return json.dumps({"answers": [{"n": n, "answer": a, "why": "t"}
                                           for n, a in zip(numbers, self.judge)]})
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


def test_a_met_checklist_leaves_achieved_alone(tmp_path, monkeypatch) -> None:
    """Ломка наоборот: всё «да» — вердикт не трогается."""
    monkeypatch.setenv(ENV, "1")
    agent, _ = _run(tmp_path, _Llm(["YES", "YES", "YES"], _ANSWER))
    assert agent.episodic_store.load()[-1].completion_state == "achieved"


def test_a_failed_judge_is_not_an_accusation(tmp_path, monkeypatch) -> None:
    """Судья упал — «не знаю»; программный пункт (VERDICT: есть) остался «да»."""
    monkeypatch.setenv(ENV, "1")
    agent, events = _run(tmp_path, _Llm([], _ANSWER, fail_judge=True))
    verdict = next(e["payload"] for e in events if e["event"] == "checklist_verdict")
    assert verdict["unmet_important"] == []
    assert agent.episodic_store.load()[-1].completion_state == "achieved"


def test_switched_off_costs_no_model_calls(tmp_path, monkeypatch) -> None:
    """Аварийный выключатель =0 — ни одного лишнего вызова, ход как прежде."""
    monkeypatch.setenv(ENV, "0")
    llm = _Llm(["NO", "NO", "NO"], _ANSWER)
    agent, events = _run(tmp_path, llm)
    assert not {"request_checklist", "checklist_verdict"} & set(_names(events))
    assert not any(c["system"] in (BUILD_SYSTEM, JUDGE_SYSTEM) for c in llm.calls)
    assert agent.episodic_store.load()[-1].completion_state == "achieved"


def test_an_exact_item_is_checked_by_program_not_by_the_judge() -> None:
    """RLCF: подстрока проверяется программой; судья её не видит."""
    llm = _Llm(["YES", "YES", "YES"], "")
    checklist = build_checklist(llm, _REQUEST)
    assert checklist.items[-1].question == UNIVERSAL
    verdict = check_answer(llm, _REQUEST, checklist, "без нужной строки")
    by_q = {v.item.question: v for v in verdict.verdicts}
    assert by_q["Есть строка VERDICT:?"].how == "program"
    assert by_q["Есть строка VERDICT:?"].answer == "no"
    assert "Есть строка VERDICT:?" not in llm.calls[-1]["user"]
    assert "Есть строка VERDICT:?" in verdict.unmet


def test_an_item_without_a_quote_from_the_request_is_dropped() -> None:
    """Замер 24.09 на 16 живых парах: важное «нет» в 14, и большинство — за
    выдуманные «подразумеваемые» требования (извинись, пообещай, разговорный
    тон). Пункт держится, только если его цитата дословно стоит в поручении."""
    checklist = build_checklist(_Llm([], ""), _REQUEST)
    questions = [i.question for i in checklist.items]
    assert "Извинился ли агент?" not in questions
    assert "Не больше трёх предложений?" in questions, "лишний пробел в цитате не в счёт"
    assert "unquoted dropped: 1" in checklist.reason


def test_the_universal_item_cannot_lower_the_verdict_alone() -> None:
    checklist = Checklist(items=(ChecklistItem(UNIVERSAL, UNIVERSAL_IMPORTANCE),), reason="t")
    assert check_answer(_Llm(["NO"], ""), _REQUEST, checklist, "ответ").unmet == ()


def test_a_minor_item_answered_no_is_not_unmet() -> None:
    """Вес ниже порога — «нет» записано, но вердикт не понижает."""
    checklist = Checklist(items=(ChecklistItem("Объяснено ли, что такое BM25?", 20),), reason="t")
    verdict = check_answer(_Llm(["NO"], ""), _REQUEST, checklist, "ответ")
    assert verdict.verdicts[0].answer == "no" and verdict.unmet == ()


@pytest.mark.parametrize("raw", ["", "не json", '{"items": []}', '{"items": [{"question": ""}]}'])
def test_an_unusable_checklist_is_empty_not_invented(raw: str) -> None:
    class _Raw(FakeLLM):
        def complete(self, system, user, **kwargs):
            return raw
    assert build_checklist(_Raw(), _REQUEST).items == ()


def test_the_rule_never_raises_a_worse_verdict() -> None:
    for declared in ("blocked", "failed", "partially_achieved"):
        verdict = assemble_completion_verdict(
            aborted_reason="", replan_exhausted=False, declared=declared, checklist_unmet=True)
        assert verdict.state == declared


def test_a_judge_abstaining_is_not_a_no() -> None:
    """Замер 24.09, #544: пункт «не меняй код» — про действие, не про текст ответа;
    судья ответил «нет, в ответе нет инструкции». Теперь судья может сказать N/A."""
    checklist = Checklist(items=(ChecklistItem("Код не изменён?", 95),), reason="t")
    verdict = check_answer(_Llm(["N/A"], ""), _REQUEST, checklist, "ответ")
    assert verdict.verdicts[0].answer == "unknown" and verdict.unmet == ()


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


def test_a_minor_no_is_not_asked_twice() -> None:
    llm = _Llm(["NO"], "")
    check_answer(llm, _REQUEST, Checklist(items=(ChecklistItem("Мелочь?", 10),), reason="t"), "ответ")
    assert sum(c["system"] == JUDGE_SYSTEM for c in llm.calls) == 1


def test_it_is_on_by_default(monkeypatch) -> None:
    """Решение оператора 24.09: починка работает сразу, переменная только выключает."""
    from core.request_checklist import enabled
    monkeypatch.delenv(ENV, raising=False)
    assert enabled()
    monkeypatch.setenv(ENV, "0")
    assert not enabled()
