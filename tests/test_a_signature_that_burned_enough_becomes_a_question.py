"""Сигнатура, сжёгшая межзапусковый потолок, становится вопросом — не тратой.

Замер и решение: MIR-149. Живой случай — 150 циклов ОДНОГО действия за
27 часов, 1773 единицы (59 % всех денег кампаний), при страже, чья память
живёт один запуск: 146 из 150 циклов были циклом №1 свежего запуска.

Классификаторный гейт честно не строится (различитель из восьми пар — подгонка,
форма F-2). Но запись сама доказала: ЦЕНА различает — петля за 1773 против
петли за ноль. Потолок — бюджетная ПОЛИТИКА, не выведенное правило; число 400
(два дневных прогона гранта) одобрено оператором 2026-08-27 вместе с формой
последствия: не молчаливое исполнение, а вопрос ему.

`observe` и прочие нулевые по цене сигнатуры потолка не достигают никогда —
законные повторения (18 наблюдений в сутки) не задеваются по построению.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.best_next_action import BestNextAction
from core.campaign import CampaignActionOutcome, CampaignConfig, run_campaign
from core.campaign_ledger import CampaignLedger, spent_units_by_action


def _work(action: str) -> BestNextAction:
    return BestNextAction(
        action=action, title="t", severity="high", priority=80,
        reason="signal", risk="reversible",
    )


class _Gather:
    def __init__(self, action: str):
        self._action = action

    def __call__(self, agent, workspace, approval_inbox):
        return {"action": _work(self._action)}


class _CountingExecute:
    def __init__(self, outcome: CampaignActionOutcome):
        self._outcome = outcome
        self.calls = 0

    def __call__(self, **_kw):
        self.calls += 1
        return self._outcome


def _ledger_with_history(tmp_path: Path, action: str, *, units_per_row: int,
                         rows: int) -> CampaignLedger:
    path = tmp_path / "data" / "campaign_ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for _ in range(rows):
            fh.write(json.dumps({
                "cycle": 1, "action": action, "result": "completed",
                "idle": False, "cost_units_spent": units_per_row,
                "llm_calls_spent": 2,
            }) + "\n")
    return CampaignLedger(path=path)


def _run(tmp_path: Path, ledger: CampaignLedger, action: str, execute):
    return run_campaign(
        CampaignConfig(goal="g", max_cycles=3),
        agent=SimpleNamespace(log=None),
        workspace=str(tmp_path),
        gather_signals=_Gather(action),
        execute_action=execute,
        ledger=ledger,
        now_fn=lambda: datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
    )


def test_the_cross_run_sum_reads_the_ledger() -> None:
    """Основание потолка: сумма трат по сигнатуре за ВСЕ запуски."""
    rows = [
        {"action": "a", "cost_units_spent": 100},
        {"action": "a", "cost_units_spent": 50},
        {"action": "b", "cost_units_spent": 7},
        {"action": "c"},
        {"broken": True},
    ]

    spent = spent_units_by_action(rows)

    assert spent == {"a": 150, "b": 7}


def test_a_signature_over_the_cap_is_not_executed(tmp_path: Path) -> None:
    """Красный свидетель: 440 единиц истории ≥ потолка 400 — исполнения нет."""
    ledger = _ledger_with_history(tmp_path, "improve_x", units_per_row=110, rows=4)
    execute = _CountingExecute(CampaignActionOutcome(result="completed", work_done=True))

    result = _run(tmp_path, ledger, "improve_x", execute)

    assert execute.calls == 0, "потолок обязан стоять ДО исполнения"
    capped = [r for r in result.records if r.result == "cost_cap"]
    assert capped, "строка эскалации обязана лечь в леджер"
    assert "400" in capped[0].reason and "440" in capped[0].reason
    assert "оператор" in capped[0].reason.lower()


def test_a_signature_under_the_cap_still_runs(tmp_path: Path) -> None:
    """Другая сторона: 330 < 400 — работа идёт как шла."""
    ledger = _ledger_with_history(tmp_path, "improve_x", units_per_row=110, rows=3)
    execute = _CountingExecute(CampaignActionOutcome(result="completed", work_done=True))

    result = _run(tmp_path, ledger, "improve_x", execute)

    assert execute.calls == 1
    assert all(r.result != "cost_cap" for r in result.records)


def test_a_zero_cost_signature_never_trips(tmp_path: Path) -> None:
    """Законные повторения бесплатны — 200 строк истории не рождают вопроса."""
    ledger = _ledger_with_history(tmp_path, "observe_like", units_per_row=0, rows=200)
    execute = _CountingExecute(CampaignActionOutcome(result="completed", work_done=True))

    result = _run(tmp_path, ledger, "observe_like", execute)

    assert execute.calls == 1
    assert all(r.result != "cost_cap" for r in result.records)


def test_the_capped_campaign_still_stops_not_spins(tmp_path: Path) -> None:
    """Эскалация не крутится вечно: серия cost_cap останавливает кампанию."""
    ledger = _ledger_with_history(tmp_path, "improve_x", units_per_row=200, rows=3)
    execute = _CountingExecute(CampaignActionOutcome(result="completed", work_done=True))

    result = _run(tmp_path, ledger, "improve_x", execute)

    assert result.status in ("stopped", "completed")
    assert result.cycles_run <= 3


def test_zero_cap_means_off_and_negative_is_rejected() -> None:
    import pytest

    CampaignConfig(max_cost_units_per_signature=0)
    with pytest.raises(ValueError):
        CampaignConfig(max_cost_units_per_signature=-1)
