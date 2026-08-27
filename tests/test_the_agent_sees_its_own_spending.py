"""Зеркало трат: пары «потратил → получил», никогда голый расход.

Замер, отвергнутые варианты и границы: MIR-177 в docs/audit/MASTER_ISSUE_REGISTRY.md.

Дисциплина из дайджеста (запись 7, Snell 2024): отчёт без знаменателя учит
«тратить больше = становиться лучше». Поэтому каждая денежная строка обязана
нести «на 100 единиц», а не сумму.
"""
from __future__ import annotations

from core.campaign_types import CampaignResult
from core.spend_report import action_spend, model_spend, spend_report_lines


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


def test_an_action_is_judged_by_completions_per_unit() -> None:
    """То же для действий кампании: цена завершения, а не сумма трат."""
    rows = [
        _cycle("study", units=72), _cycle("study", units=84),
        _cycle("propose", units=20, result="error"),
        _cycle("propose", units=30),
    ]

    table = action_spend(rows)

    by_action = {t.action: t for t in table}
    assert by_action["study"].completed == 2
    assert by_action["propose"].completed == 1
    assert by_action["propose"].units_per_completed == 50.0
    assert by_action["study"].units_per_completed == 78.0


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


def test_the_run_summary_shows_the_price_of_a_useful_cycle() -> None:
    """Всегда видимое зеркало: цена полезного цикла в сводке прогона.

    По образцу `goal_drove` (MIR-163): класть поле и молчать — MIR-138 заново.
    """
    result = CampaignResult(
        status="completed", goal="g", stop_reason="", cycles_run=3,
        records=[],
        totals={"useful_cycles": 2, "idle_cycles": 0, "repeat_cycles": 0,
                "error_cycles": 0, "llm_calls": 4, "cost_units": 156,
                "proposals": 0, "artifacts": 1, "goal_drove_cycles": 0},
    )

    assert "units_per_useful=78" in result.user_summary()


def test_an_idle_run_does_not_invent_a_price() -> None:
    """Ноль полезных циклов — цена не определена, а не бесконечна и не нулевая."""
    result = CampaignResult(
        status="completed", goal="g", stop_reason="", cycles_run=1,
        records=[],
        totals={"useful_cycles": 0, "idle_cycles": 1, "repeat_cycles": 0,
                "error_cycles": 0, "llm_calls": 0, "cost_units": 0,
                "proposals": 0, "artifacts": 0},
    )

    assert "units_per_useful=-" in result.user_summary()


def test_the_mirror_is_wired_into_the_operator_budget_command() -> None:
    """Проводка: орган без читателя — MIR-138; читатель — там, куда уже смотрят."""
    import inspect

    from app import operator_status as mod

    assert callable(getattr(mod, "_spend_mirror_lines", None))
    assert "_spend_mirror_lines" in inspect.getsource(mod._handle_operator_budget)


def test_the_organ_speaks_the_producers_vocabulary() -> None:
    """Успех считается словом производителя, а не словом автора органа.

    Живой словарь журнала: success/error (997/395 на 1392 строках). Первая
    версия считала выдуманное «ok» — и зеркало показало 0 успехов у всех
    моделей. Найдено взглядом на живой вывод, а не тестами: подделки несли ту
    же выдумку, что и орган ([[coverage-copied-from-the-neighbour]]).
    """
    invented = [{"role": "r", "provider": "p", "model": "m",
                 "cost_units": 10, "status": "ok"}]
    real = [{"role": "r", "provider": "p", "model": "m",
             "cost_units": 10, "status": "success"}]

    assert model_spend(invented)[0].ok_calls == 0
    assert model_spend(real)[0].ok_calls == 1
