"""R4 и R5 — два решения оператора 2026-08-13.

R4: «сфабрикованные цитаты терминальны». Живой случай R-E1 ход 2
(`run_7b1b2caf`): ответ «36 × 3 = 108» с двумя цитатами [dialogue:previous]
на диалог, которого не существовало, ушёл оператору как есть — усечение не
добрало кусков, категориальная ветка спала за флагом, а для фабрикации ветки
не было вовсе. Определение одно и уже существует: fabricated ==
`cited_but_unmatched` (`core/evidence_support.py`). Фабрикация — свойство
текста, не эвристика: рубеж срабатывает независимо от режима раскатки.

R5: «включай referent_resolver». Орган построен, выключен переменной, которую
никто не выставлял; тот же живой ход показал цену: анафора «результат
предыдущего шага» при ПУСТОЙ истории пошла в планирование и взяла чужие 36 из
опыта. Включение + недостающее ребро: анафора без антецедента — это вопрос
оператору ДО планирования, через существующие ворота уточнений.
"""
from __future__ import annotations

from pathlib import Path

from core.evidence import ProvenanceChain, make_evidence
from core.ids import new_trace_id
from core.logger import TraceLogger
from core.loop import AgentLoop
from core.memory import WorkingMemory
from core.policy import PolicyGate
from core.referent_resolver import referent_resolver_mode
from core.unsupported_claims import apply_answer_enforcement
from core.verifier import verify
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry

_FABRICATED = (
    "Conclusion: 36 умножить на 3 равно 108. [dialogue:previous]\n"
    "Facts:\n- Результат предыдущего шага был 36 [dialogue:previous]\n"
    "Sources:\n1. dialogue:previous - предыдущий шаг\n"
    "Confidence: high\nUnverified: nothing\n"
)


def _report(answer: str):
    chain = ProvenanceChain()
    chain.add(make_evidence(kind="file", source_id="probe/inv.txt",
                            obtained_via="t", claim="",
                            excerpt="qty: 5\n", confidence=0.9))
    return verify(answer=answer, chain=chain, user_question="умножь на три")


def test_a_fabricated_citation_is_terminal() -> None:
    """ГЛАВНОЕ R4: цитата на несуществующий источник не уезжает оператору."""
    report = _report(_FABRICATED)
    assert report.cited_but_unmatched_chunks >= 1, "фикстура сломалась"
    enf = apply_answer_enforcement(
        answer=_FABRICATED, report=report, question="умножь на три",
    )
    assert enf.outcome == "citation_integrity", enf.outcome
    assert enf.applied is True
    assert "108" not in enf.answer.splitlines()[0], (
        "тело с фабрикацией ушло первой строкой как есть"
    )


def test_a_resolved_answer_is_untouched() -> None:
    """ПРЕДОХРАНИТЕЛЬ R4: разрешившиеся цитаты рубеж не трогает."""
    ok = (
        "Conclusion: qty равно 5. [file:probe/inv.txt]\n"
        "Facts:\n- qty равно 5 [file:probe/inv.txt]\n"
        "Sources:\n1. file:probe/inv.txt - inv\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    enf = apply_answer_enforcement(answer=ok, report=_report(ok), question="qty?")
    assert enf.outcome != "citation_integrity"


def test_the_signal_reaches_the_learning_law() -> None:
    from core.smart_memory import DISQUALIFYING_DEFECT_SIGNALS

    assert "citation_fabricated" in DISQUALIFYING_DEFECT_SIGNALS


def test_the_resolver_is_on_by_default(monkeypatch) -> None:
    """R5: орган включён рождением, а не переменной, которую никто не ставил."""
    monkeypatch.delenv("AGENT_REFERENT_RESOLVER", raising=False)
    assert referent_resolver_mode() == "on"
    monkeypatch.setenv("AGENT_REFERENT_RESOLVER", "off")
    assert referent_resolver_mode() == "off"


def test_anaphora_without_an_antecedent_asks_first(tmp_path: Path) -> None:
    """R5, конец цепи: «предыдущий шаг» при пустой истории — вопрос, не план.

    Живой ход взял чужие 36 из опыта и надписал их диалогом. Ворота уточнений
    стоят ДО планирования — планировщик не должен быть вызван вовсе.
    """
    registry = ToolRegistry()
    planner = FakePlanner(sources=[])
    agent = AgentLoop(
        registry=registry, policy=PolicyGate(registry),
        llm=FakeLLM(responses=["x"] * 4),
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=tmp_path / "logs",
                           verbose=False),
        planner=planner, memory=WorkingMemory(), max_replan_attempts=1,
    )
    answer = agent.run(
        "Умножь qty этой позиции на три, используя результат предыдущего шага."
    )
    assert planner.calls == [], "анафора без антецедента дошла до планировщика"
    assert "предыдущ" in answer.lower(), answer


def test_a_present_antecedent_is_not_questioned(tmp_path: Path) -> None:
    """ПРЕДОХРАНИТЕЛЬ R5: при живой истории уточнение не выпрашивается."""
    _ANSWER = (
        "Conclusion: ok. [general-knowledge]\n"
        "Facts:\n- ok [general-knowledge]\n"
        "Sources:\n1. general-knowledge - general-knowledge\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    registry = ToolRegistry()
    planner = FakePlanner(sources=[])
    agent = AgentLoop(
        registry=registry, policy=PolicyGate(registry),
        llm=FakeLLM(responses=[_ANSWER] * 8),
        logger=TraceLogger(trace_id=new_trace_id(), log_dir=tmp_path / "logs",
                           verbose=False),
        planner=planner, memory=WorkingMemory(), max_replan_attempts=1,
    )
    agent.run("Посчитай сумму qty на полке B2.")
    agent.run("Раздели результат предыдущего шага на два.")
    assert len(planner.calls) == 2, "уточнение выпрошено при живом антецеденте"


def test_a_task_clause_is_not_supplied_text() -> None:
    """R5-включение вскрыло: критический глагол + хвост задачи резолвился в
    user_text, и критика-без-объекта съедала содержательную задачу (без роли,
    инструментов, памяти). Принесённый текст — многострочный или в кавычках."""
    from core.referent_resolver import ReferentResolver, is_local_critique_eligible

    r = ReferentResolver()
    kw = {"prior_turns": (), "artifacts": (), "file_hint": None,
          "current_session_id": "s", "current_turn_id": "t"}
    task = r.resolve("проанализируй архитектуру проекта и предложи план "
                     "рефакторинга слоя памяти с обоснованием", **kw)
    assert not is_local_critique_eligible(task)

    pasted = r.resolve(
        "проанализируй этот текст:\nПервая строка.\nВторая строка выводов.",
        **kw,
    )
    if pasted.status == "resolved" and pasted.primary is not None:
        assert is_local_critique_eligible(pasted)

