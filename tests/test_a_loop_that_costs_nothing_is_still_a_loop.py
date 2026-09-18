"""Работа без трат — всё ещё работа: её подпись банится, причина доезжает, счёт не врёт.

Замер живого прогона 2026-09-20 (кампания владельца, `max_cycles=20`):
девять циклов подряд `failed action=run_claim_experiment llm=0 cost=0`,
и в сводке при этом `errors=0`. След `trace_78713d6a80ad69a9faaaf79697c8003e`
объясняет девятку дословно: `causal_experiment_inconclusive` ×9
(`claim_key: cclaim_b922d54b5774`, `target: reasoning_action_check`,
«следствие не воспроизвелось ни в одном рукаве») и следом
`causal_climb_declined` ×9 («эксперименты не дали ни одного вердикта»).

Цепочка до корня, каждое звено проверено по живому коду:

1. `_decline` отдаёт `result="failed"` с нулями и БЕЗ предмета, хотя ключ
   заявки лежит рядом, в `payload["claim_key"]`, а `run_claim_experiment`
   стоит в `campaign._SUBJECT_AWARE_ACTIONS`.
2. `CampaignActionOutcome.ran` читает только траты и продукт, поэтому
   отработавший вхолостую эксперимент «попыткой» не считается.
3. `campaign.py` банит подпись только `if outcome.ran`, значит подпись не
   банится НИКОГДА, заявка остаётся ровно такой же годной для
   `experimentable_claims`, и следующий цикл повторяет тот же прогон.

Храповик повторов оказался устроен наоборот: `propose_engineering_task`,
который ТРАТИТ модель, получил `repeat` со второго захода, а бесплатный
`run_claim_experiment` крутился до конца прогона. Дороже всех обходится
действие, которое ничего не стоит.

Контроли ниже стерегут то, что ломать нельзя: отказ ДО старта («нет заявок
со спецификациями эксперимента») попыткой по-прежнему не является — иначе
кампания солжёт «прежний проход не снял сигнал» там, где прохода не было
(MIR-117). Стенд повторяет tests/test_campaign.py: сборка курьером.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import core.causal_climb_action as climb
from core.campaign import run_campaign
from core.campaign_ledger import CampaignCycleRecord, CampaignLedger
from core.campaign_types import CampaignActionOutcome, CampaignConfig, CampaignResult

_CLAIM_KEY = "cclaim_b922d54b5774"
_TARGET = "reasoning_action_check"
_SPEC = f"[exp: {_TARGET} | A=рукав с причиной | B=рукав без причины | след=различие]"


# --------------------------------------------------------------------------
# 1. Контракт исхода: работа без трат
# --------------------------------------------------------------------------

def test_work_that_spent_nothing_is_still_an_attempt() -> None:
    """Свидетель: отработавшее вхолостую действие называет себя попыткой.

    Два рукава эксперимента реально исполнились, вердикта не дали, денег не
    стоили. Если такой исход не попытка, его подпись не забанят и он вернётся
    следующим циклом — девять раз, как в замере.
    """
    outcome = CampaignActionOutcome(result="failed", attempted=True)

    assert outcome.ran, (
        "два рукава отработали, а исход не считает себя попыткой: "
        "подпись не забанится и цикл вернётся тем же"
    )
    assert not outcome.did_work, "продукта не было — полезным цикл не стал"


def test_a_refusal_before_the_start_is_still_not_an_attempt() -> None:
    """Контроль: отказ до старта попыткой не стал (MIR-117).

    «Нет заявок со спецификациями эксперимента» — это отказ, когда работать
    было не над чем. Забанить его подпись значило бы соврать «прежний проход
    не снял сигнал» там, где прохода не было.
    """
    assert not CampaignActionOutcome(result="failed").ran
    assert not CampaignActionOutcome(result="failed", attempted=False).ran


def test_the_outcome_carries_the_reason_it_ended() -> None:
    """Свидетель: исход несёт СВОЮ причину, а не повод себя выбрать."""
    outcome = CampaignActionOutcome(
        result="failed", attempted=True,
        note="эксперименты не дали ни одного вердикта",
    )

    assert outcome.note == "эксперименты не дали ни одного вердикта"


# --------------------------------------------------------------------------
# 2. Живой путь: эксперимент без вердикта
# --------------------------------------------------------------------------

class _Explanation:
    """Живое объяснение со спецификацией — ровно то, что отбирает суд."""

    def __init__(self) -> None:
        self.alive = True
        self.predicts = _SPEC
        self.statement = "предполагаемая причина"
        self.refuted_by = ""


def _pending_claim():
    claim = SimpleNamespace(
        chosen="", intervention=None, refuted_reason="",
        explanations=(_Explanation(),),
    )
    return ((claim, {"key": _CLAIM_KEY, "directive": "", "machine_action": ""}),)


def test_an_inconclusive_experiment_reports_that_it_ran(monkeypatch) -> None:
    """Свидетель: живой путь замера говорит «работа была» и называет предмет.

    Рукава возвращают строку без следа — тот самый «не воспроизвелось ни в
    одном рукаве» из следа прогона. Исход обязан нести и признак попытки,
    и ключ заявки: без ключа кампания забанит голое имя действия и остановит
    ВСЕ заявки разом, а не ту одну, что крутится.
    """
    monkeypatch.setattr(climb, "experimentable_claims", lambda ws: _pending_claim())
    monkeypatch.setitem(climb._EXPERIMENT_TARGETS, _TARGET, lambda arm: "следа нет")

    outcome = climb.run_claim_experiment(agent=SimpleNamespace(log=None),
                                         workspace="/tmp/ws")

    assert outcome.result == "failed"
    assert outcome.attempted, (
        "оба рукава исполнились, а исход молчит о попытке — "
        "подпись не забанится и заявка вернётся следующим циклом"
    )
    assert outcome.subject == _CLAIM_KEY, (
        f"предмет обязан быть ключом заявки, получено {outcome.subject!r}: "
        "иначе стопор накроет чужие заявки"
    )
    assert outcome.note == "эксперименты не дали ни одного вердикта"


def test_an_empty_docket_still_refuses_before_the_start(monkeypatch) -> None:
    """Контроль: пустой список заявок — по-прежнему отказ ДО старта."""
    monkeypatch.setattr(climb, "experimentable_claims", lambda ws: ())

    outcome = climb.run_claim_experiment(agent=SimpleNamespace(log=None),
                                         workspace="/tmp/ws")

    assert outcome.result == "failed"
    assert not outcome.attempted, "работать было не над чем — попытки не было"
    assert not outcome.ran


# --------------------------------------------------------------------------
# 3. Кампания: девятый одинаковый цикл не наступает
# --------------------------------------------------------------------------

class _AlwaysSameSignal:
    def __call__(self, agent, workspace, approval_inbox, goal="",
                 exhausted_actions=frozenset()):
        return {"action": SimpleNamespace(
            action="run_claim_experiment",
            title="run the experiment",
            severity="info", priority=40, risk="low",
            grounds="open claims carry two-arm experiment specs",
            decided_by="rule",
            reason="Open causal claims carry two-arm experiment specs",
        )}


class _InconclusiveExecute:
    """Отрабатывает вхолостую всегда — как в замере."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *, agent, workspace, action, config, approval_inbox=None):
        self.calls += 1
        return CampaignActionOutcome(
            result="failed", attempted=True, subject=_CLAIM_KEY,
            note="эксперименты не дали ни одного вердикта",
        )


def _run(execute, *, max_cycles=20, max_idle_streak=3, ledger=None):
    return run_campaign(
        CampaignConfig(max_cycles=max_cycles, max_idle_streak=max_idle_streak,
                       dry_run=False, idle_recheck_seconds=0),
        agent=SimpleNamespace(log=None),
        workspace="/tmp/ws",
        gather_signals=_AlwaysSameSignal(),
        execute_action=execute,
        ledger=ledger or CampaignLedger(),
        now_fn=lambda: datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc),
    )


def test_the_same_fruitless_experiment_does_not_fill_the_whole_run() -> None:
    """Свидетель: девять одинаковых циклов не наступают.

    В замере действие крутилось до потолка прогона. Забанив подпись вместе с
    ключом заявки, кампания видит стопор и уходит, а не жжёт остаток цикла
    за циклом.
    """
    execute = _InconclusiveExecute()

    result = _run(execute)

    assert execute.calls < 9, (
        f"вхолостую отработавшее действие исполнено {execute.calls} раз — "
        "в замере таких было девять подряд"
    )
    assert result.stop_reason.startswith("no_progress_stall"), (
        f"кампания обязана назвать застой, назвала {result.stop_reason!r}"
    )


def test_a_refusal_before_the_start_is_not_mistaken_for_a_stall() -> None:
    """Контроль: отказ до старта застоем не объявляется.

    Тот же стенд, но исход честно говорит «попытки не было». Подпись банить
    нечего, и кампания не смеет записать застой — её остановит потолок
    циклов, а не выдуманный стопор.
    """
    class _NeverRan(_InconclusiveExecute):
        def __call__(self, **kwargs):
            super().__call__(**kwargs)
            return CampaignActionOutcome(result="failed", note="нет заявок")

    result = _run(_NeverRan(), max_cycles=5)

    assert not result.stop_reason.startswith("no_progress_stall"), (
        "прохода не было — застой объявлять не о чем"
    )


# --------------------------------------------------------------------------
# 4. Реестр: причина падения доезжает до записи
# --------------------------------------------------------------------------

def test_the_ledger_records_why_the_cycle_failed() -> None:
    """Свидетель: причина отказа лежит в записи кампании, а не только в журнале.

    В замере поле `reason` девяти падений занято ПОВОДОМ ВЫБРАТЬ действие
    («Open causal claims carry two-arm experiment specs…»), а почему цикл
    упал — не записано ни разу. Читателю реестра девять падений выглядят
    беспричинными.
    """
    ledger = CampaignLedger()

    _run(_InconclusiveExecute(), ledger=ledger)

    failed = [r for r in ledger.records if r.result == "failed"]
    assert failed, "стенд обязан был дать хотя бы одно падение"
    assert failed[0].outcome_reason == "эксперименты не дали ни одного вердикта", (
        f"причина падения не доехала до записи: {failed[0].outcome_reason!r}"
    )
    assert "two-arm" in failed[0].reason, (
        "повод выбрать действие обязан остаться на месте: это разные вопросы"
    )
    assert failed[0].to_dict()["outcome_reason"], (
        "причина обязана пережить перезапуск — строка реестра пишется из to_dict"
    )


def test_the_failed_line_says_why_out_loud() -> None:
    """Свидетель: человек видит причину в той же строке, где видит падение."""
    record = CampaignCycleRecord(
        cycle=7, ts="2026-09-20T12:00:00+00:00", goal="g",
        action="run_claim_experiment", action_title="t", severity="info",
        priority=40, risk="low", idle=False, llm_calls_spent=0,
        cost_units_spent=0, result="failed",
        reason="Open causal claims carry two-arm experiment specs",
        outcome_reason="эксперименты не дали ни одного вердикта",
    )

    line = record.user_summary()

    assert "эксперименты не дали ни одного вердикта" in line, (
        f"строка падения молчит о причине: {line}"
    )


def test_a_finished_cycle_does_not_grow_a_reason() -> None:
    """Контроль: удавшийся цикл строку не удлиняет."""
    record = CampaignCycleRecord(
        cycle=1, ts="t", goal="g", action="a", action_title="t",
        severity="info", priority=1, risk="low", idle=False,
        llm_calls_spent=2, cost_units_spent=72, result="completed",
        reason="повод", outcome_reason="",
    )

    assert record.user_summary().endswith("cost=72")


# --------------------------------------------------------------------------
# 5. Сводка: девять падений не показываются нулём
# --------------------------------------------------------------------------

def test_the_summary_does_not_report_nine_failures_as_none() -> None:
    """Свидетель: сводка называет падения вслух.

    В замере стояло `errors=0` при девяти `failed`. Формально верно — счётчик
    считает вылетевшие исключения, — а человеку это ложь: прогон, где каждый
    второй цикл упал, отчитался как безошибочный.
    """
    result = _run(_InconclusiveExecute())

    assert result.totals.get("failed_cycles"), (
        "падения не сосчитаны: сводке нечего показать человеку"
    )
    assert f"failed={result.totals['failed_cycles']}" in result.user_summary(), (
        f"сводка молчит о падениях: {result.user_summary().splitlines()[2]}"
    )


def test_the_exception_counter_keeps_its_own_meaning() -> None:
    """Контроль: `errors` остался счётчиком исключений, а не переехал.

    Падение действия и вылетевшее из цикла исключение — разные события.
    Слить их в одно число значило бы потерять то, ради чего стоит
    `max_consecutive_errors`.
    """
    result = _run(_InconclusiveExecute())

    assert result.totals["error_cycles"] == 0, (
        "исключений не было — счётчик исключений обязан остаться нулём"
    )


def test_a_clean_run_shows_no_failures() -> None:
    """Контроль: прогон без падений показывает ноль, а не пустоту."""
    summary = CampaignResult(
        status="completed", goal="g", stop_reason="", cycles_run=1,
        totals={"failed_cycles": 0},
    ).user_summary()

    assert "failed=0" in summary
