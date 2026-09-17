"""Выбор цели оплачивается из кармана кампании (живой прогон 18.09 01:44).

Замер прогона `trace_f24a00c45aa4a20c77686b069208ba8b`, двадцать циклов по
хартии. Реестр расходов за эти двадцать минут: **13 вызовов, 195 единиц
стоимости, 61 644 токена**. Кампания в `campaign_stop` о себе:
``totals={'llm_calls': 3, 'cost_units': 0}``.

Разница разложилась без остатка. В журнале прогона: один `[CHARTER] goal:`
(первичный выбор), семь `[CHARTER] next goal:` и два `[CHARTER] no next
goal:` — **десять** обращений к модели за целью. 3 + 10 = 13, ровно реестр.

Причина в `core/campaign.py`: `_switch_goal` зовёт `next_goal()` (строка 267)
— это полноценный вызов модели ценой около пяти тысяч токенов, — а счёт
кампании на строках 642-643 складывает ИСКЛЮЧИТЕЛЬНО `outcome.*_spent`, то
есть только то, что потратил цикл. Смена цели не проходит ни через один
`CampaignActionOutcome`, и потому не попадает в счёт никогда.

Это вторая дыра того же счётчика. Первую (траты «рук» внутри цикла) закрыл
PR #345; арифметика выше — доказательство, что закрыта была не вся.

Цена слепоты та же, что и в прошлый раз, и в этом прогоне она сработала:
`--max-cost-units 300` был выставлен, реально ушло 195 из 300, а затвор
видел ноль и не мог вмешаться. Отказ цели тоже стоит денег: два обращения
«no next goal» — это два оплаченных ответа модели, которые не дали ничего.

Мерка здесь одна и она честная: **счёт кампании обязан сойтись с тем, что
реально ушло с реестра.** Не «примерно», не «хотя бы часть» — сойтись.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from core.campaign import run_campaign
from core.campaign_ledger import CampaignLedger
from core.campaign_types import CampaignActionOutcome, CampaignConfig

_NOW = datetime(2026, 9, 18, 1, 44, tzinfo=timezone.utc)


class _Ledger:
    """Реестр, который считает по-настоящему, — как настоящий."""

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
        log=None,
        model_router=SimpleNamespace(usage_ledger=SimpleNamespace(budget_ledger=ledger)),
    )


def _action(name: str = "propose_engineering_task", priority: int = 59):
    return SimpleNamespace(
        action=name, title=name, severity="medium", priority=priority,
        risk="reversible", grounds="operator_goal", decided_by="test",
        next_check_at=None, reason="",
    )


class _Gather:
    """Цикл, который работает, но никуда не продвигается: так и зовут смену."""

    def __init__(self, idle: bool = True) -> None:
        self.idle = idle

    def __call__(self, agent, workspace, approval_inbox, goal="",
                 exhausted_actions=frozenset()):
        return {"action": _action("observe", priority=0) if self.idle else _action()}


class _Execute:
    """Цикл тратит на реестре ровно столько, сколько объявляет в исходе."""

    def __init__(self, ledger: _Ledger, calls: int = 1, cost: int = 2) -> None:
        self.ledger, self.calls, self.cost = ledger, calls, cost
        self.runs = 0

    def __call__(self, *, agent, workspace, action, config, approval_inbox=None):
        self.runs += 1
        self.ledger.spend(calls=self.calls, cost=self.cost)
        return CampaignActionOutcome(
            result="completed", llm_calls_spent=self.calls,
            cost_units_spent=self.cost, subject="the-subject", work_done=False,
        )


class _Picker:
    """Выбор цели — такой же платный вызов модели, как и всё остальное."""

    def __init__(self, ledger: _Ledger, goals, calls: int = 5, cost: int = 7) -> None:
        self.ledger, self.calls, self.cost = ledger, calls, cost
        self.goals = iter(goals)
        self.asked = 0

    def __call__(self) -> str:
        self.asked += 1
        self.ledger.spend(calls=self.calls, cost=self.cost)
        return next(self.goals, "")


def _run(tmp_path, *, ledger, gather, execute, next_goal, max_cycles=8):
    return run_campaign(
        CampaignConfig(goal="первая цель", max_cycles=max_cycles,
                       max_idle_streak=2, dry_run=False, cycle_pause_seconds=0,
                       max_wall_clock_seconds=0, max_llm_calls=0,
                       max_cost_units=0),
        agent=_agent(ledger),
        workspace=str(tmp_path),
        gather_signals=gather, execute_action=execute,
        ledger=CampaignLedger(), next_goal=next_goal,
        now_fn=lambda: _NOW, sleep_fn=lambda seconds: None,
        approval_inbox=None,
    )


# ── свидетели ─────────────────────────────────────────────────────────────


def test_what_choosing_a_goal_spends_reaches_the_campaigns_bill(tmp_path) -> None:
    """Семь смен цели стоили денег, а в счёт попал только цикл.

    Живой прогон: десять обращений за целью, ноль из них в счёте.
    """
    ledger = _Ledger()
    execute = _Execute(ledger)
    picker = _Picker(ledger, ["вторая цель", "третья цель", "четвёртая цель"])

    result = _run(tmp_path, ledger=ledger, gather=_Gather(), execute=execute,
                  next_goal=picker)

    assert picker.asked > 0, "смена цели ни разу не позвалась — рига не та"
    assert result.totals["llm_calls"] == ledger.calls, (
        f"реестр отдал {ledger.calls} вызовов, кампания записала "
        f"{result.totals['llm_calls']}: {picker.asked} обращений за целью "
        "прошли мимо счёта"
    )
    assert result.totals["cost_units"] == ledger.cost, (
        f"реестр отдал {ledger.cost} единиц, кампания записала "
        f"{result.totals['cost_units']}"
    )


def test_a_refused_switch_still_bills_what_it_spent(tmp_path) -> None:
    """Отказ стоит столько же, сколько согласие.

    Два «no next goal» в живом прогоне — это два оплаченных ответа модели,
    не давших ничего. Молчание источника целей не делает вызов бесплатным.
    """
    ledger = _Ledger()
    execute = _Execute(ledger)
    picker = _Picker(ledger, [])  # источник молчит: каждая попытка — отказ

    result = _run(tmp_path, ledger=ledger, gather=_Gather(), execute=execute,
                  next_goal=picker)

    assert picker.asked > 0, "отказ ни разу не позвался — рига не та"
    assert result.totals["llm_calls"] == ledger.calls, (
        f"отказ стоил {picker.asked * picker.calls} вызовов и не попал в счёт: "
        f"реестр {ledger.calls}, кампания {result.totals['llm_calls']}"
    )


# ── контроли ──────────────────────────────────────────────────────────────


def test_a_run_without_a_goal_picker_bills_exactly_its_cycles(tmp_path) -> None:
    """Контроль: без выбора цели счёт как был — трата цикла, и ровно она.

    Если этот контроль покраснеет, правка считает что-то дважды.
    """
    ledger = _Ledger()
    execute = _Execute(ledger)

    result = _run(tmp_path, ledger=ledger, gather=_Gather(idle=False),
                  execute=execute, next_goal=None)

    assert execute.runs > 0
    assert result.totals["llm_calls"] == execute.runs * execute.calls
    assert result.totals["llm_calls"] == ledger.calls
    assert result.totals["cost_units"] == ledger.cost


def test_a_cycle_that_spends_nothing_bills_nothing(tmp_path) -> None:
    """Контроль: пустая трата остаётся нулём, а не становится мусором."""
    ledger = _Ledger()
    execute = _Execute(ledger, calls=0, cost=0)
    picker = _Picker(ledger, ["вторая цель"], calls=0, cost=0)

    result = _run(tmp_path, ledger=ledger, gather=_Gather(), execute=execute,
                  next_goal=picker)

    assert result.totals["llm_calls"] == 0
    assert result.totals["cost_units"] == 0
