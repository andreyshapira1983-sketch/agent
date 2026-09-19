"""Выбор, отданный неназванному человеку, — вопрос до работы, а не догадка.

Операторское задание 2026-08-03 (MIR-075): «он НЕ переспрашивает, он сразу
начинает делать». Замер 2026-09-19 (рабочий экзамен): «Сделай для клиента
отчёт по data.csv в нужном ему формате и отправь ему» — агент выбрал Markdown
сам и объявил отправку невозможной, не спросив. Оператор решил: сначала
спрашивать. Точность правила измерена на 90 настоящих просьбах (75 экзаменационных,
15 живых из журналов агента): сработало ровно на этой одной.
"""
from __future__ import annotations

import pytest

from core.completion_contract import derive_completion_contract


@pytest.mark.parametrize("request_text", [
    "Сделай для клиента отчёт по файлу data.csv в нужном ему формате и отправь ему.",
    "Подготовь выгрузку в формате, который нужен заказчику.",
    "Оформи таблицу как клиенту удобно.",
    "Prepare the summary in the format the client needs and send it to the client.",
])
def test_a_deferred_choice_becomes_a_question(request_text: str):
    contract = derive_completion_contract(request_text)
    assert contract.needs_clarification, request_text
    assert all("?" in a for a in contract.ambiguities), "неясность — готовый вопрос заказчику"


@pytest.mark.parametrize("request_text", [
    "Отправь мне отчёт по data.csv в формате Markdown.",
    "Отправь файл sum.txt по электронной почте на client@example.com.",
    "Сохрани отчёт в удобном формате.",
    "Посчитай сумму чисел в numbers.txt и запиши её в sum.txt.",
    "Подготовь письмо заказчику о сдаче работы — letter.md. Ничего не отправляй.",
])
def test_a_named_or_own_choice_is_not_asked(request_text: str):
    assert not derive_completion_contract(request_text).needs_clarification, request_text


def test_the_gate_asks_before_any_work(workspace):
    from core.approval import AutoApprover
    from core.logger import TraceLogger
    from core.loop import AgentLoop, new_trace_id
    from core.policy import PolicyGate
    from tests.conftest import FakeLLM, FakePlanner
    from tools.base import ToolRegistry

    registry = ToolRegistry()
    planner = FakePlanner(sources=[])
    loop = AgentLoop(
        registry=registry, policy=PolicyGate(registry), llm=FakeLLM(responses=["x"] * 3),
        logger=TraceLogger(new_trace_id(), workspace / "logs", verbose=False),
        planner=planner, approval_provider=AutoApprover(default="approve"),
    )
    answer = loop.run("Сделай для клиента отчёт по файлу data.csv в нужном ему формате и отправь ему.")
    assert "Уточнение перед выполнением" in answer and "формате" in answer, answer
