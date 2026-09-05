"""A quoted term inside a work order is not an analysis target (R7, 2026-09-05).

Work order 1 («flights TLV→BER», docs/audit/WORK_ORDER_FLIGHTS_2026-09-05.md):
the order carried «изначально показанная» — 21 characters inside 770 of
directive, and the directive contained «покажи». The referent resolver took
the quote as an explicit_quote analysis target, the loop went down the
local-critique path: planner skipped, no tool ran, the synthesizer answered
the 21 characters with four invented citations, the answer was withheld.
Rubric 0/10 as scored; basket five — not his, the loop never planned.

Pinned here: a short quote drowned in a long directive is a term; a brought
quote (long, or the directive is about it) still reaches the critique path;
and the order itself, through the loop, reaches the planner.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.referent_resolver import ReferentCandidate, ReferentDecision, is_local_critique_eligible

ORDER = (
    "Рабочий заказ. Найди варианты перелёта Тель-Авив → Берлин: вылет 15 сентября 2026, обратно "
    "25 сентября 2026. 1 взрослый, эконом, только ручная кладь. Сравни минимум 3 доступных "
    "источника. Покажи 5 лучших вариантов по полной цене и длительности. Для каждого укажи: "
    "источник, авиакомпанию, рейсы, пересадки, багаж, валюту, итоговую стоимость и время проверки. "
    "Ничего не покупать и не бронировать. Не обходить CAPTCHA, антибот-защиту и ограничения сайтов: "
    "если источник не пускает — пометь его BLOCKED и иди дальше. Поисковую карточку не считать "
    "подтверждённой ценой: если цена на финальном шаге отличается, отчётная цена — финальная, а "
    "первая — «изначально показанная». Если цена не подтверждается на финальном шаге — пометь её "
    "как неподтверждённую, а не выдавай как факт."
)


def _decision(target: str, directive: str) -> ReferentDecision:
    primary = ReferentCandidate(kind="explicit_quote", id="quote:x", provenance="question_quote",
                                relevance_score=0.92, trust="user_data", label="quoted_span", excerpt=target)
    return ReferentDecision(status="resolved", candidates=(primary,), primary=primary,
                            analysis_target_excerpt=target, directive_excerpt=directive)


def test_a_short_quote_drowned_in_a_long_directive_is_a_term_not_a_target():
    assert not is_local_critique_eligible(_decision("изначально показанная", ORDER))


def test_a_brought_quote_still_reaches_the_critique_path():
    quote = "Планировщик выбирает веб-поиск всякий раз, когда в вопросе есть слово «сейчас», даже про себя."
    assert is_local_critique_eligible(_decision(quote, f"Разбери слабые места этого фрагмента: «{quote}»"))
    # short quote, short directive: the directive is about the quote — still eligible
    assert is_local_critique_eligible(_decision("жёлудь-17", "Покажи слабые места в «жёлудь-17»"))


def test_the_order_itself_reaches_the_planner_through_the_loop(tmp_path: Path):
    from core.logger import TraceLogger
    from core.loop import AgentLoop, new_trace_id
    from core.memory import WorkingMemory
    from core.planner import LLMPlanner
    from core.policy import PolicyGate
    from tests.conftest import FakeLLM
    from tools.base import ToolRegistry
    from tools.file_read import FileReadTool

    plan = json.dumps({"reasoning": "search the web", "steps": []})
    synth = ("Conclusion: no sources reached. [general-knowledge]\nFacts:\n- none [general-knowledge]\n"
             "Sources:\n1. general-knowledge\nConfidence: low\nUnverified: everything\n")
    llm = FakeLLM(responses=[plan, synth])
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=tmp_path))
    logger = TraceLogger(trace_id=new_trace_id(), log_dir=tmp_path / "logs", verbose=False)
    agent = AgentLoop(registry=registry, policy=PolicyGate(registry), llm=llm, logger=logger,
                      planner=LLMPlanner(llm=llm, registry=registry), memory=WorkingMemory())

    agent.run(user_question=ORDER)

    events = [json.loads(line).get("event") for p in (tmp_path / "logs").glob("trace_*.jsonl")
              for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert "local_critique_path" not in events, "the order must not be read as a critique of its own quote"
    assert any("PLANNER_MODE" in c["system"] for c in llm.calls), "the planner must be asked"
