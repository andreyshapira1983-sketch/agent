"""Запрет, который распознан и никем не исполнен, — хуже нераспознанного.

ЖИВОЙ СЛУЧАЙ 2026-08-10. Оператор запретил обращаться к внешним моделям и
потребовал: если внешняя модель уже вызвана до того, как запрет можно
применить, — доложить это как архитектурный результат. Система запрет УВИДЕЛА
(`completion_contract ... unsupported_deliverables=[{'kind': 'prohibition'}],
coverage=partial`) и через полсекунды позвала `anthropic/claude-sonnet-4-5`.

Различение «я этого обязательства не умею представлять» появилось накануне и
было СДЕЛАНО ДИАГНОСТИЧЕСКИМ: грепом подтверждено, что `unsupported_deliverables`
не читает никто вне своего модуля. Производитель есть, смысл есть, потребителя
нет.

ЧТО ЗДЕСЬ НЕ ДЕЛАЕТСЯ. Ворота не блокируют ход. Модуль честно не умеет
проверять запрет, а значит и судить о его нарушении не может; блокировать по
непроверяемому признаку значило бы выдумать обязательство — то самое, что
`derive_completion_contract` отказывается делать по своему контракту. Здесь
чинится единственное, что доказано: нераспознанный запрет обязан дойти ДО
ОПЕРАТОРА в ответе, а не остаться в журнале.
"""
from __future__ import annotations

from core.completion_contract import derive_completion_contract
from core.unsupported_claims import apply_answer_enforcement

_PROHIBITING = (
    "Perform a local-autonomy stress test. For this part, you must not use "
    "Anthropic, OpenAI or any remote LLM API. Do not repair or modify the "
    "architecture during this run. Report separately what executed locally."
)
_ANSWER = (
    "Conclusion: разбор выполнен. [file]\n"
    "Facts:\n- Цикл вызвал планировщик и синтезатор. [file]\n"
    "Sources:\n1. file - core/loop.py\n"
    "Confidence: high\nUnverified: nothing\n"
)


class _Report:
    total_chunks = 4
    verified_chunks = 4
    unverified_chunks = 0
    cited_but_unmatched_chunks = 0
    self_declared_chunks = 0
    structural_chunks = 0
    topic_supported_but_claim_unverified_chunks = 0
    subagent_asserted_chunks = 0
    receipt_missing_chunks = 0
    dialogue_supported_chunks = 0
    user_asserted_chunks = 0
    chain_was_empty = False
    fully_unverified = False
    malformed_output = False
    chunks: tuple = ()


def test_the_prohibition_is_still_recognised() -> None:
    """ПРЕДУСЛОВИЕ: без этого остальное зелено по постороннему поводу."""
    contract = derive_completion_contract(_PROHIBITING)
    kinds = {u.kind for u in contract.unsupported_deliverables}
    assert "prohibition" in kinds
    assert contract.coverage == "partial"


def test_an_unrepresentable_prohibition_reaches_the_answer() -> None:
    """ГЛАВНОЕ: оператор узнаёт о запрете, который система не проверяла."""
    contract = derive_completion_contract(_PROHIBITING)
    result = apply_answer_enforcement(
        answer=_ANSWER, report=_Report(), question=_PROHIBITING, contract=contract
    )
    assert "запрет" in result.answer.lower() or "prohibition" in result.answer.lower(), (
        "нераспознанный запрет остался в журнале и до оператора не дошёл"
    )
    assert result.applied


def test_a_request_without_prohibitions_is_not_annotated() -> None:
    """Ломка наоборот: заметка не должна появляться на обычном запросе."""
    contract = derive_completion_contract("что в файле doc.txt")
    result = apply_answer_enforcement(
        answer=_ANSWER, report=_Report(), question="что в файле doc.txt",
        contract=contract,
    )
    assert "запрет" not in result.answer.lower()


def test_the_contract_argument_is_optional() -> None:
    """Прежние вызывающие не сломаны: без контракта поведение как было."""
    result = apply_answer_enforcement(
        answer=_ANSWER, report=_Report(), question=_PROHIBITING
    )
    assert result.answer == _ANSWER


def test_the_note_names_the_quoted_evidence() -> None:
    """Заметка обязана называть, ЧТО именно она считает запретом."""
    contract = derive_completion_contract(_PROHIBITING)
    result = apply_answer_enforcement(
        answer=_ANSWER, report=_Report(), question=_PROHIBITING, contract=contract
    )
    evidence = next(u.evidence for u in contract.unsupported_deliverables
                    if u.kind == "prohibition")
    assert evidence.lower() in result.answer.lower()
