"""Зеркало трат: пары «потратил → получил», никогда голый расход.

Замер, отвергнутые варианты и границы: MIR-177 в docs/audit/MASTER_ISSUE_REGISTRY.md.

Дисциплина из дайджеста (запись 7, Snell 2024): отчёт без знаменателя учит
«тратить больше = становиться лучше». Поэтому каждая денежная строка обязана
нести «на 100 единиц», а не сумму.
"""
from __future__ import annotations

from core.spend_report import model_spend, spend_report_lines


def _call(model: str, *, units: int, ok: bool = True) -> dict:
    return {
        "role": "synthesizer", "provider": "openai", "model": model,
        "cost_units": units, "status": "success" if ok else "error",
    }


def _cycle(action: str, *, units: int, result: str = "completed") -> dict:
    return {"action": action, "cost_units_spent": units, "result": result}


def test_a_model_is_judged_by_outcome_per_unit_not_by_total() -> None:
    """Красный свидетель: 1392 живые строки расхода не читало ни одно зеркало.

    Дешёвая модель с 8 успехами за 40 единиц ЛУЧШЕ дорогой с 9 за 90 — по
    успехам на сотню единиц (20 против 10), хотя по голым успехам хуже.
    """
    rows = [_call("cheap", units=5) for _ in range(8)] + [
        _call("dear", units=10) for _ in range(9)
    ]

    table = model_spend(rows)

    by_model = {t.model: t for t in table}
    assert by_model["cheap"].ok_per_100_units > by_model["dear"].ok_per_100_units


def test_every_money_line_carries_a_denominator() -> None:
    """Дисциплина Снелла: строка про деньги без «на 100 единиц» запрещена."""
    lines = spend_report_lines(
        usage_rows=[_call("cheap", units=5)],
        ledger_rows=[_cycle("study", units=72)],
    )

    money_lines = [ln for ln in lines if "units" in ln]
    assert money_lines, "отчёт про деньги обязан говорить о деньгах"
    for ln in money_lines:
        assert ("per 100 units" in ln) or ("units/completed" in ln), ln


def test_zero_spend_does_not_divide_the_world() -> None:
    """Пустые журналы дают пустой отчёт, а не деление на ноль."""
    assert spend_report_lines(usage_rows=[], ledger_rows=[]) == []
    table = model_spend([_call("free", units=0)])
    assert table[0].ok_per_100_units == 0.0


def test_the_mirror_becomes_a_planner_block() -> None:
    """Красный свидетель: зеркало доходит до планировщика тем же путём, что опыт.

    Блок — ДАННЫЕ, не указание: он обязан сам называть себя записью, из которой
    вывод делает читатель. Записанный нами вывод был бы нашим, а не его.
    """
    from core.spend_report import spend_mirror_block

    block = spend_mirror_block(
        usage_rows=[_call("cheap", units=5)],
        ledger_rows=[_cycle("study", units=72)],
    )

    assert block.startswith("<agent_spend_mirror>")
    assert block.rstrip().endswith("</agent_spend_mirror>")
    assert "per 100 units" in block
    assert "data, not a directive" in block
