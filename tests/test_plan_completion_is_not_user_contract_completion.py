"""Выполненный план — не выполненный контракт оператора.

ЖИВОЙ ПОВТОР 2026-08-10, прогон `run_ef52f8c0`. В начале хода:
`completion_contract ... unsupported_deliverables=[experiment, prohibition],
coverage=partial` — система прямо сказала, что часть контракта оператора
представить не умеет. В конце того же хода:
`completion_obligation required=True, satisfied=True,
requirement_sources=['plan', 'acceptance_criteria']`.

`evaluate_completion_obligations` ПОЛУЧАЕТ контракт и читает у него только
`obligations`; `coverage` и `unsupported_deliverables` не читает никто. Поэтому
удовлетворённость плана выходит наружу под именем удовлетворённости задачи.

ПОЧЕМУ ЭТО НЕ ПРОСТО НАДПИСЬ. У вердикта есть зубы: `assemble_completion_verdict`
понижает заявленное `achieved` до `partially_achieved`, а это снимает и кредит
процедуре, и допуск эпизода. Отказ удостоверять непроверенное — поведение, а не
отчёт.

ЗАМЕР ОБЪЁМА, сделанный до правки: из 120 реальных вопросов оператора из
`data/episodic_memory.jsonl` частичное покрытие дают 11. Правка узкая и бьёт
ровно туда, где оператор просил то, чего система не умеет проверять, — она не
выключает обучение.
"""
from __future__ import annotations

from core.completion_contract import derive_completion_contract
from core.completion_obligation import evaluate_completion_obligations
from core.smart_memory import assemble_completion_verdict

#: Один представимый долг (файл), один непредставимый формат отчёта, один запрет.
_MIXED = (
    "Создай core/probe_demo.py и прогони тесты. "
    "At the end report separately: what executed locally; what did not. "
    "Do not use another LLM to interpret the results."
)
_PLAIN = "перечисли функции в core/loop.py"
_ANSWER = "Conclusion: сделано. [file]\nFacts:\n- сделано [file]\nSources:\n1. file - x\n"


def test_the_contract_reports_partial_for_the_mixed_request() -> None:
    """ПРЕДУСЛОВИЕ: без частичного покрытия остальное проверяет пустоту."""
    contract = derive_completion_contract(_MIXED)
    assert contract.coverage == "partial"
    kinds = {u.kind for u in contract.unsupported_deliverables}
    assert "prohibition" in kinds
    assert contract.obligations, "представимый долг обязан остаться представимым"


def test_the_verdict_carries_the_coverage_of_the_user_contract() -> None:
    """ГЛАВНОЕ: удовлетворённость плана перестаёт выходить как удовлетворённость задачи."""
    contract = derive_completion_contract(_MIXED)
    result = evaluate_completion_obligations(
        question=_MIXED, answer=_ANSWER, contract=contract
    )
    assert result.contract_coverage == "partial", (
        "финальный потребитель по-прежнему не знает, что контракт оператора "
        "охвачен лишь частично"
    )
    payload = result.to_log_payload()
    assert payload["contract_coverage"] == "partial"


def test_a_plain_request_stays_complete() -> None:
    """Ломка наоборот: обычный запрос не объявляется недовыполненным."""
    contract = derive_completion_contract(_PLAIN)
    result = evaluate_completion_obligations(
        question=_PLAIN, answer=_ANSWER, contract=contract
    )
    assert result.contract_coverage == "complete"


def test_a_partial_contract_lowers_a_claim_of_achieved() -> None:
    """Зубы: непроверенная часть контракта не удостоверяется как выполненная.

    Понижает только заявление `achieved` — как и `obligation_unmet`. Честно
    доложенные `blocked`/`failed` не ухудшаются: проблемой была не честность.
    """
    verdict = assemble_completion_verdict(
        aborted_reason="", replan_exhausted=False, declared="achieved",
        user_contract_partial=True,
    )
    assert verdict.state == "partially_achieved"
    assert verdict.overridden_by == "user_contract_unrepresented"


def test_it_never_raises_a_worse_verdict() -> None:
    """Односторонность: понижать можно, повышать нельзя."""
    for declared in ("blocked", "failed", "partially_achieved"):
        verdict = assemble_completion_verdict(
            aborted_reason="", replan_exhausted=False, declared=declared,
            user_contract_partial=True,
        )
        assert verdict.state != "achieved"


def test_full_coverage_leaves_the_verdict_alone() -> None:
    """ПРЕДОХРАНИТЕЛЬ: без частичного покрытия вердикт прежний."""
    verdict = assemble_completion_verdict(
        aborted_reason="", replan_exhausted=False, declared="achieved",
        user_contract_partial=False,
    )
    assert verdict.state == "achieved"


def test_satisfied_still_means_what_it_meant() -> None:
    """`satisfied` не переопределяется: оно про ПРЕДСТАВИМЫЕ обязательства.

    Перевернуть его в False значило бы заявить о невыполненном долге, которого
    никто не установил, — та же выдумка, только с другой стороны. Различение
    несёт отдельное поле, а не подмена смысла старого.
    """
    contract = derive_completion_contract(_MIXED)
    result = evaluate_completion_obligations(
        question=_MIXED, answer=_ANSWER, contract=contract
    )
    assert isinstance(result.satisfied, bool)
    assert result.contract_coverage == "partial"
