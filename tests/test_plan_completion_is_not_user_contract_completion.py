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

from core.smart_memory import assemble_completion_verdict


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
