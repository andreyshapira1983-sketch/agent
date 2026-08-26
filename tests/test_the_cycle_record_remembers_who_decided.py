"""Запись цикла помнит, участвовала ли цель в выборе действия.

Замер, отвергнутые варианты и границы: MIR-163 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from core.campaign_ledger import CampaignCycleRecord
from core.campaign_types import CampaignResult


def _record(**overrides) -> CampaignCycleRecord:
    values = {
        "cycle": 1,
        "ts": "2026-08-26T00:00:00+00:00",
        "goal": "Проследить core/loop.py и описать его места вызова",
        "action": "improve_failure_to_idea_pipeline",
        "action_title": "Resolve the open self-improvement issue",
        "severity": "medium",
        "priority": 55,
        "risk": "read_only",
        "idle": False,
        "llm_calls_spent": 1,
        "cost_units_spent": 39,
        "result": "completed",
        "reason": "a durable issue remains unresolved",
    }
    values.update(overrides)
    return CampaignCycleRecord(**values)


def test_the_record_carries_the_grounds_of_the_choice() -> None:
    """Красный свидетель: решение ЗНАЛО основание и не доносило его до записи.

    Замер 2026-08-26 по 267 живым циклам: чтобы узнать, вела ли цель работу,
    пришлось пересчитывать заново — в строке цикла этого не было. Из 267
    предмет назван в 9 (3 %), а действие порождено целью в 38 (14 %).
    """
    row = _record(grounds="retained_record", decided_by="sole_candidate").to_dict()

    assert row["grounds"] == "retained_record"
    assert row["decided_by"] == "sole_candidate"


def test_an_unrecorded_ground_is_named_not_guessed() -> None:
    """Умолчание говорит «не записано», а не выдумывает основание."""
    row = _record().to_dict()

    assert row["grounds"] == "unrecorded"
    assert row["decided_by"] == "unrecorded"


def test_the_run_summary_says_how_often_the_goal_drove() -> None:
    """Оператор видит число прямо в сводке прогона, а не по запросу.

    Отвергнут вариант «положить поле и молчать»: запись без читателя — это
    MIR-138 заново.
    """
    result = CampaignResult(
        status="completed", goal="цель", stop_reason="", cycles_run=3,
        records=[],
        totals={"useful_cycles": 1, "idle_cycles": 0, "repeat_cycles": 2,
                "error_cycles": 0, "llm_calls": 1, "cost_units": 39,
                "proposals": 0, "artifacts": 0, "goal_drove_cycles": 1},
    )

    assert "goal_drove=1" in result.user_summary()
