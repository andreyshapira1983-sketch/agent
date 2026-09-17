"""Деньги, потраченные руками цикла, стоят в счёте этого цикла.

Живой прогон 2026-09-17, 23:09–23:11 (трасса
`logs/trace_8e1679b20f50062864a08d947ed07941.jsonl`), двадцать один цикл
кампании. Реестр бюджета за это окно:

    llm_calls 19 | model_tokens 97 429 | model_cost_units 114

Собственные книги кампании за тот же прогон (`campaign_stop`):

    "totals": {"llm_calls": 0, "cost_units": 0, ...}

Ноль. Все девятнадцать вызовов сделали инженерные руки, а руки работают ПОСЛЕ
того, как снят счётчик. В `core/campaign_io.py::_default_execute_action`
показания берутся дважды — `llm_before` перед рабочей сессией и `llm_after`
сразу после неё, — а `_engineering_hands`, `_propose_repair_from_diagnosis`,
`_propose_hypothesis_from_study` и `_propose_doctrine_draft` вызываются ниже
второго замера. Всё, что они тратят, в разность не попадает никогда.

Чем это платится, кроме неверного числа в журнале:

* `--max-llm-calls` не работает. Тот прогон шёл под потолком 20 вызовов,
  сделал 19 и остановился по `shift_limit:max_cycles=20`, потому что его
  собственный счётчик стоял на нуле.
* `--max-cost-units` и `max_cost_units_per_signature` слепы по той же причине:
  в `signature_spend` едет `outcome.cost_units_spent`, то есть нуль.
* Детектор петли получает `llm_calls_spent: 0`. В том прогоне он сработал
  четырнадцать раз подряд и каждый раз видел бесплатную петлю. Бесплатную
  петлю незачем прекращать — она стоила треть дневного окна.

Сюда НЕ входит спор о том, что цель назвала один файл, а руки взяли другой:
это отдельный дефект, он починен в PR #342 в 23:45 того же вечера, то есть
после этого прогона. Здесь речь только о счётчике.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.campaign_types import CampaignConfig


class _Ledger:
    """Реестр, который считает по-настоящему, как настоящий."""

    def __init__(self) -> None:
        self.calls = 0
        self.cost = 0

    def spend(self, *, calls: int, cost: int) -> None:
        self.calls += calls
        self.cost += cost

    def snapshot(self) -> dict:
        return {"totals": {"llm_calls": self.calls, "model_cost_units": self.cost}}


def _agent(ledger: _Ledger):
    return SimpleNamespace(
        model_router=SimpleNamespace(usage_ledger=SimpleNamespace(budget_ledger=ledger)),
        log=None,
        llm=None,
    )


def _action(name: str = "propose_engineering_task"):
    return SimpleNamespace(
        action=name, title="t", reason="r", evidence=(), target_path=None,
    )


class _SilentRuntime:
    """Рабочая сессия, которая не тратит ничего.

    Так изолируется спор: всё, что окажется в счёте, потратили руки.
    """

    def __init__(self, agent, *, workspace, approval_inbox=None):
        self.agent = agent

    def run(self, config):
        return SimpleNamespace(
            semantic_result=lambda: ("completed", True),
            tasks=[],
            approvals=[],
        )


@pytest.fixture()
def _rig(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import core.autonomous_runtime as rt
    import core.campaign_io as mod
    from core.approval_inbox import ApprovalInbox

    monkeypatch.setattr(rt, "AutonomousRuntime", _SilentRuntime)
    monkeypatch.setattr(mod, "_goal_answer_and_digest", lambda report: ("a", None))
    monkeypatch.setattr(mod, "_approvals_born", lambda inbox, before: None)
    monkeypatch.setattr(mod, "_propose_repair_from_diagnosis",
                        lambda **kw: None)

    ledger = _Ledger()
    agent = _agent(ledger)
    inbox = ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl")

    def _run(action_name: str = "propose_engineering_task"):
        return mod._default_execute_action(
            agent=agent,
            workspace=tmp_path,
            action=_action(action_name),
            config=CampaignConfig(goal="g", max_cycles=1, dry_run=False),
            approval_inbox=inbox,
        )

    return SimpleNamespace(mod=mod, ledger=ledger, run=_run, monkeypatch=monkeypatch)


def test_what_the_engineering_hands_spend_reaches_the_cycles_bill(_rig) -> None:
    """Девятнадцать вызовов рук записались в реестр и не записались в цикл.

    Числа взяты из живого прогона один к одному: 19 вызовов, 114 единиц. Если
    счётчик снимается до рук, разность равна нулю при любой трате.
    """
    _rig.monkeypatch.setattr(
        _rig.mod, "_engineering_hands",
        lambda **kw: (_rig.ledger.spend(calls=19, cost=114),
                      "engineering_proposed:ain_test")[1],
    )

    outcome = _rig.run()

    assert outcome.llm_calls_spent == 19, (
        "руки сделали 19 вызовов, цикл записал "
        f"{outcome.llm_calls_spent}: счётчик снят до рук"
    )
    assert outcome.cost_units_spent == 114, outcome.cost_units_spent


def test_what_the_repair_hands_spend_reaches_the_cycles_bill(_rig) -> None:
    """Тот же шов, другие руки — чтобы правка не оказалась заплаткой на одном.

    `_propose_repair_from_diagnosis` вызывается из того же места и ниже того же
    второго замера. Починить только инженерные руки значит оставить дыру
    открытой для ремонтных, учебных и документных.
    """
    _rig.monkeypatch.setattr(
        _rig.mod, "_propose_repair_from_diagnosis",
        lambda **kw: (_rig.ledger.spend(calls=3, cost=7),
                      "repair_proposed:ain_test")[1],
    )

    outcome = _rig.run("investigate_something")

    assert outcome.llm_calls_spent == 3, (
        f"ремонтные руки потратили 3 вызова, цикл записал {outcome.llm_calls_spent}"
    )
    assert outcome.cost_units_spent == 7, outcome.cost_units_spent


def test_a_cycle_that_spent_nothing_still_bills_nothing(_rig) -> None:
    """Контроль: пустая трата остаётся нулём, а не становится мусором."""
    outcome = _rig.run()

    assert outcome.llm_calls_spent == 0
    assert outcome.cost_units_spent == 0


def test_what_the_work_session_spends_was_always_counted(_rig) -> None:
    """Контроль: старое поведение цело.

    Трата САМОЙ рабочей сессии попадала в счёт и до правки. Если этот тест
    покраснеет, правка сдвинула первый замер, а не второй.
    """
    class _SpendingRuntime(_SilentRuntime):
        def run(self, config):
            _rig.ledger.spend(calls=2, cost=5)
            return super().run(config)

    import core.autonomous_runtime as rt
    _rig.monkeypatch.setattr(rt, "AutonomousRuntime", _SpendingRuntime)

    outcome = _rig.run()

    assert outcome.llm_calls_spent == 2
    assert outcome.cost_units_spent == 5
