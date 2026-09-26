"""24/48h autonomous work campaign engine.

* An IDLE cycle never calls the LLM. When there is no high-priority action
the agent records ``reason_if_idle`` and stamps an advisory
``next_check_at`` on the ledger record instead of asking a model "what
should I do" (which would cost money to be told "nothing").
``next_check_at`` is informational only — actual cycle pacing (including
idle cycles) is driven by ``cycle_pause_seconds``; nothing re-reads
``next_check_at`` to skip or delay a cycle. * ``max_idle_streak``
consecutive no-progress cycles ends the campaign with a report — as
``healthy_idle`` (status ``completed``) when every cycle in the streak was a
pure priority-0 observation, or as ``idle_stall`` / ``no_progress_stall``
(status ``stopped``) when the streak contains repeat cycles: work was wanted
and went nowhere, ask the operator. * The campaign opens NO new effect path.
A useful cycle runs through the existing
:class:`~core.autonomous_runtime.AutonomousRuntime`, which already routes
every effect through PolicyGate + the approval inbox. Dry-run is the
default. * Budgets act at two layers (MIR-116, fixed 2026-08-22): the loop
checks its counters between cycles, and ``max_cost_units`` additionally
travels into each useful cycle as a run cost envelope, so the model-call
pre-flight gate refuses the spend that would pass the cap — the bound acts
before the next spend, not one cycle later. Injected collaborators that
merely REPORT spend bypass the gate and are bounded only by the
between-cycle check.

This module is deliberately split into a *pure loop* (``run_campaign``) plus
two injectable collaborators (``gather_signals`` and ``execute_action``).
The defaults wire the real signal-gathering and the real bounded runtime
pass; tests inject deterministic fakes and assert on the real record shapes.
"""
from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from core.best_next_action import GOAL_GROUNDS, BestNextAction
from core.campaign_io import (
    _cost_totals,
    _default_execute_action,
    _default_gather_signals,
    _log,
)
from core.campaign_ledger import (
    CampaignCycleRecord,
    CampaignLedger,
    load_ledger_rows,
    spent_units_by_action,
)
from core.campaign_types import CampaignActionOutcome, CampaignConfig, CampaignResult
from core.campaign_verdict import judge_and_record, judge_campaign
from core.run_context import run_cost_envelope
from core.self_stop_record import record_self_stop, record_stop_observation
from core.wake_events import wake_mark, woken_by

# Предметный страж повторов — авторство агента (DEDUP_DESIGN/DEDUP_TIMING,
# WEAVE ред.2 §1): белый список статичен и известен до исполнения.
_SUBJECT_AWARE_ACTIONS = frozenset({
    "explain_causal_observation",
    "discriminate_causal_claim",
    "run_claim_experiment",
    "birth_experiment_specs",
})
_MAX_STEPS_PER_ACTION = 10
#: Пустых исходов одного действия подряд (ни работы, ни трат — `failed`,
#: `approval_wait` и любой другой), после которых оно пропускается. Вечер
#: суточного прогона: 12 циклов подряд `propose_engineering_task` →
#: `approval_wait` («на файл уже есть заявка») — страж ловил только `failed`.
#: Суточный прогон 2026-09-19: `run_claim_experiment` 25 раз за 12 минут дал
#: «следствие не воспроизвелось» — ноль вызовов модели, `ran` ложно, и ни
#: банк подписей, ни потолок шагов его не видели: оба считают только
#: ОТРАБОТАВШЕЕ действие.
_MAX_FAILED_REPEATS = 3

CampaignStatus = Literal["completed", "stopped"]


def _session_cost_units(agent: Any) -> int | None:
    """The session cost counter the run envelope bounds, or None when the host
    carries no usage ledger to measure it by (injected test agents)."""
    try:
        ledger = getattr(agent.model_router, "usage_ledger", None)
        if ledger is None:
            return None
        return int(ledger.session_cost_units())
    except (AttributeError, TypeError, ValueError):
        return None


def _cycle_cost_envelope(agent: Any, config: CampaignConfig, cost_units_used: int):
    """MIR-116: one useful cycle's spend bound — the session counter's current
    value plus the campaign's remaining budget — as a run cost envelope, so the
    cap acts at the model-call gate BEFORE the next spend. Without a cap, or
    without a usage ledger to measure the session by, a nullcontext leaves the
    between-cycle check as the only bound (prior behaviour, unchanged)."""
    if not config.max_cost_units:
        return nullcontext()
    session_cost = _session_cost_units(agent)
    if session_cost is None:
        return nullcontext()
    return run_cost_envelope(
        allowed_total_units=session_cost
        + (config.max_cost_units - cost_units_used)
    )


def _cross_run_signature_spend(ledger: CampaignLedger | None) -> dict[str, int]:
    """Траты по сигнатурам за все прежние запуски; без файла — пустая память."""
    if ledger is None or ledger.path is None:
        return {}
    try:
        return spent_units_by_action(load_ledger_rows(ledger.path))
    except OSError:
        return {}


def _cost_cap_record(*, cycle: int, ts: str, goal: str, action: BestNextAction,
                     spent: int, cap: int,
                     success_check: str = "") -> CampaignCycleRecord:
    """Строка эскалации MIR-149: потолок пал — исполнение становится вопросом.

    Потолок — бюджетная политика (число 400 одобрено оператором 2026-08-27),
    не выведенный различитель: классификатор из восьми живых пар был бы
    подгонкой под породившие его случаи. Нулевые по цене сигнатуры (observe)
    сюда не попадают по построению.
    """
    return CampaignCycleRecord(
        cycle=cycle, ts=ts, goal=goal, success_check=success_check,
        action=action.action, action_title=action.title,
        severity=action.severity, priority=action.priority,
        risk=action.risk, grounds=action.grounds, decided_by=action.decided_by,
        idle=False, llm_calls_spent=0, cost_units_spent=0,
        result="cost_cap",
        work_done=False,
        reason=(
            f"сигнатура '{action.action}' потратила {spent} единиц за все "
            f"запуски при потолке {cap}; исполнение остановлено — нужно слово "
            f"оператора: поднять потолок (max_cost_units_per_signature) или "
            f"закрыть сигнал иначе"
        ),
    )


def _verifier_llm(agent: Any) -> Any:
    """Модель-проверяющий для судьи содержания (core/goal_content_judge.py) или None."""
    try:
        return agent.model_router.for_role("verifier")
    except Exception:  # noqa: BLE001 — нет роутера (тесты, сухой прогон) — судья по файлам
        return None


def goal_met_now(goal: str, success_check: str, workspace: Any, started_at: Any, llm: Any = None) -> bool:
    """Сошёлся ли критерий цели свежим следом — после КАЖДОГО рабочего цикла.

    Ночь 24→25.09: объяснение наблюдения записывалось в первом цикле, а
    кампания судила цель только при смене: крутила «объяснять нечего», ловила
    повтор, спала 7.5 мин (25 мин — 2 полезных цикла из 13). Судья читает мир
    без модели, вердикт здесь не пишется — его пишет judge_closing_goal при
    смене. Не знаем — не меняем.
    """
    if not str(success_check or "").strip():
        return False
    try:
        since = started_at.timestamp()
        verdict = judge_campaign(goal=goal, success_check=success_check,
                                 workspace=workspace, since=since, llm=llm)
    except Exception:  # noqa: BLE001 — судья не валит цикл
        return False
    return verdict.get("verdict") == "verified"


def judge_closing_goal(agent: Any, *, workspace: Any, goal: str, success_check: str,
                       cycle: int, why: str, started_at: Any, ts: Any,
                       proposals: int = 0, artifacts: int = 0) -> dict[str, Any] | None:
    """Осудить закрывающуюся цель её же критерием и записать показание.

    Судья стоял ТОЛЬКО на выходе из прогона, поэтому за 2685 циклов вердиктов
    записано три, а 754 цикла «сделано» не проверил никто: «сделано» держалось
    на слове исполнителя (замер 2026-09-23, слово оператора «почистить всё
    враньё»). Цель закрывается в момент смены — здесь её критерий ещё в руках,
    и здесь же известно, когда она началась, чтобы чужой след ей не зачёлся.

    Цель без критерия не судится: «критерий не назван» — честное состояние, и
    выдумывать мерку задним числом хуже, чем её не иметь.
    """
    if not str(success_check or "").strip():
        return None
    try:
        verdict, error = judge_and_record(
            goal=goal, success_check=success_check, workspace=workspace,
            started_at=started_at, ts=ts, stop_reason=why, cycles_run=cycle,
            proposals=proposals, artifacts=artifacts, llm=_verifier_llm(agent),
        )
    except Exception as exc:  # noqa: BLE001 — показание не валит прогон
        _log(agent, "campaign_goal_verdict_failed",
             {"cycle": cycle, "error": repr(exc)[:200]})
        return None
    _log(agent, "campaign_goal_verdict", {
        "cycle": cycle, "goal": str(goal)[:200], "verdict": verdict.get("verdict"),
        "reason": str(verdict.get("reason") or "")[:200], "unrecorded": error,
    })
    return verdict


def _approved_ids(approval_inbox) -> frozenset[str]:
    """Approved item ids, or empty when the inbox is absent or unreadable —
    a new approval during a backoff is a wake condition (block 8)."""
    if approval_inbox is None:
        return frozenset()
    try:
        return frozenset(str(i.id) for i in approval_inbox.list(status="approved"))
    except Exception:  # noqa: BLE001 — a reading hiccup must not fake a wake or a stop
        return frozenset()


#: Действие «сама цель»: один заход агента на выбранную им цель, когда меню не
#: дало ей ничего допустимого (CampaignConfig.pursue_goal_when_idle).
PURSUE_GOAL = "pursue_goal"


def _pursue_goal_action(idle: BestNextAction, reason: str = "") -> BestNextAction:
    """Простой по цели превращается в работу над целью."""
    return BestNextAction(
        action=PURSUE_GOAL,
        title="Work on the chosen goal directly",
        severity="medium",
        priority=1,
        reason=reason or ("no menu action binds the goal (" + str(idle.reason or "")[:200]
                          + "); the goal itself is the work"),
        evidence=tuple(idle.evidence[:3]),
        risk="reversible",
        grounds="operator_goal",
        decided_by="no_candidate",
    )


def _effects_count(agent: Any) -> int:
    return len(getattr(agent, "compensation_log", None) or ())


#: Сколько заходов подряд без записи получает цель человека, прежде чем стоп по простою.
OPERATOR_GOAL_EMPTY_PASSES = 3
_EMPTY_ROUND_MARK = "~operator_goal_empty_pass_"


def _reopen_operator_goal(action: BestNextAction, config: CampaignConfig, agent: Any,
                          effects_before: int, attempted: set[str]) -> None:
    """Цель человека получает новый заход; стоп — после трёх заходов подряд без записи."""
    if action.action != PURSUE_GOAL or config.goal_is_self:
        return
    marks = {m for m in attempted if m.startswith(_EMPTY_ROUND_MARK)}
    if _effects_count(agent) > effects_before:
        attempted.difference_update(marks)
    elif len(marks) + 1 < OPERATOR_GOAL_EMPTY_PASSES:
        attempted.add(f"{_EMPTY_ROUND_MARK}{len(marks) + 1}")
    else:
        return
    attempted.discard(PURSUE_GOAL)


def _goal_first(action: BestNextAction, attempted: set[str], goal_action: str = "",
                goal_is_self: bool = False) -> BestNextAction:
    """Режим «цель первой» (CampaignConfig.goal_first): поломка — меню, иначе цель.

    `goal_is_self` только НАЗЫВАЕТ происхождение цели и ничего не решает:
    до 2026-09-21 здесь стояло `operator_goal` у всякой цели, и сводка
    докладывала человеку его цели там, где он не ставил ни одной.
    """
    grounds = "self_goal" if goal_is_self else "operator_goal"
    if action.severity in ("critical", "high"):
        return action
    if goal_action and goal_action not in attempted:
        return BestNextAction(
            action=goal_action, title="Work on the chosen goal by its own action",
            severity="medium", priority=1,
            reason=f"goal first: the goal names {goal_action!r} as the work",
            risk="reversible", grounds=grounds, decided_by="goal_first")
    if not goal_action and PURSUE_GOAL not in attempted:
        return _pursue_goal_action(action, "goal first: the goal itself is the work; the menu would "
                                           f"have taken {action.action!r} ({action.grounds})")
    return BestNextAction(
        action="observe", title="The goal had its pass", severity="none", priority=0,
        reason="goal first: the goal had its pass this cycle series; the next goal comes from the drives",
        grounds=grounds, decided_by="goal_first")


def _call_gather(gather, agent, workspace, approval_inbox, **kwargs):
    """Позвать сборщик сигналов, отдав ему ТОЛЬКО понятные ему доводы.

    До 2026-09-21 здесь стояла лесенка из `try/except TypeError`: каждый новый
    довод добавлял ступеньку, и добавление `goal_is_self` сразу это показало —
    сборщик, не знающий нового имени, проваливался на две ступени вниз и терял
    `exhausted_actions`, то есть исчерпанные действия переставали доезжать до
    выбора (поймано тестами про исчерпанное действие). Подпись спрашивается
    один раз у самого сборщика: чужой сборщик получает то, что умеет принять,
    и ничего не теряет по дороге.
    """
    try:
        params = inspect.signature(gather).parameters
    except (TypeError, ValueError):
        params = None
    if params is not None and not any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        kwargs = {k: v for k, v in kwargs.items() if k in params}
    return gather(agent, workspace, approval_inbox, **kwargs)


def _repeat_reason(action_name, hit_ceiling, failed_in_a_row=0):
    """Return the reason a repeated action is being skipped, based on whether it hit the per-campaign step ceiling."""
    if failed_in_a_row:
        return (f"'{action_name}' came back empty {failed_in_a_row} times in a row (no work, no spend); "
                "repeating it cannot change the result — skipping")
    if hit_ceiling:
        return f"потолок шагов действия за кампанию: {_MAX_STEPS_PER_ACTION} — одно действие не монополизирует прогон"
    return f"already attempted '{action_name}' this campaign; the earlier pass did not clear the signal — skipping re-execution"


def _refusal_reason(action_name: str) -> str:
    """Слова для отказа НА ВОРОТАХ — не те же, что для повтора попытки."""
    return (f"'{action_name}': ворота отказали до старта, и разрешение с тех пор "
            "не появилось — повторное предложение ничего не изменит")


def _refused_again(signature: str, refused: set[str], *, ran: bool, result: str) -> bool:
    """True, если это действие уже отказывали на воротах в этой серии циклов.

    Замер 2026-09-19: 170 циклов подряд с `blocked` на одном действии, ноль
    вызовов модели. Подпись не банилась, потому что отказ до старта попыткой
    не считается (см. CampaignActionOutcome.ran) — и это верно. Но из «нельзя
    назвать это попыткой» следовало «не помнить вовсе», а отказ на воротах —
    самостоятельный факт: путь сейчас закрыт. Память о нём отдельная, чтобы
    не врать про несуществовавший проход.
    """
    if ran or result != "blocked":
        return False
    if signature in refused:
        return True
    refused.add(signature)
    return False


def _bank_signature(agent, signature, outcome, subject_aware, attempted, action_steps):
    """Return True if the action is stalled (subject already banked), else False, banking the appropriate composite or bare signature."""
    if not subject_aware:
        attempted.add(signature)
        return False
    action_steps[signature] = action_steps.get(signature, 0) + 1
    subject = getattr(outcome, "subject", "") or ""
    banked = f"{signature}:{subject}" if subject else signature
    stalled = banked in attempted
    if stalled:
        _log(agent, "campaign_subject_stalled", {"action": signature, "subject": subject})
    attempted.add(banked)
    return stalled


def _utc_now() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)


GatherSignals = Callable[[Any, Any, Any], dict[str, Any]]
ExecuteAction = Callable[..., CampaignActionOutcome]


#: Сколько раз за один прогон агент вправе сменить исчерпанную цель.
#: Не безлимит: смена — это признание «дело кончилось», и если признаний
#: слишком много, прогон честнее закончить, чем перебирать темы.
_MAX_GOAL_SWITCHES = 12
#: Попыток выбрать следующую цель при смене — столько же, сколько на старте
#: (agent_tick._GOAL_PICK_ATTEMPTS): одна попытка убивала прогон на любом
#: молчании модели (L6, аудит 2026-09-03).
_GOAL_SWITCH_ATTEMPTS = 3


def goal_pick_attempts() -> int:
    """Попыток выбрать цель — на старте и при смене; `AGENT_GOAL_PICK_ATTEMPTS`.

    Суточный прогон 2026-09-19: при перезапуске все три попытки отверг страж
    новизны («goal repeats a recent campaign goal»), и прогон честно
    остановился. Больше попыток — больше шансов, что агент САМ найдёт новую
    тему (отклонённые варианты он видит); по умолчанию прежние 3, потолок 20.
    """
    import os

    try:
        n = int(os.environ.get("AGENT_GOAL_PICK_ATTEMPTS") or _GOAL_SWITCH_ATTEMPTS)
    except ValueError:
        n = _GOAL_SWITCH_ATTEMPTS
    return max(1, min(20, n))
#: Ожидание внутри смены (блок 8, слово оператора 2026-09-03): исход
#: отдельной работы — не конец смены. Когда три попытки сменить цель
#: отвергнуты, процесс не умирает, а ждёт ограниченно: основное условие
#: пробуждения — журнал перемен мира (data/capability_events.jsonl) и новое
#: одобрение в ящике; периодическая перепроверка — страховка от
#: пропущенного события («event-driven waiting + missed event = агент умер
#: очень правильно»). Замер вечера: три прогона по 4, 21 и 8 циклов, каждый
#: погашен исходом работы и уснул до триггера через 12 часов.
_BACKOFF_STEP_SECONDS = 60
_BACKOFF_MAX_SECONDS = 900


def _ask_for_a_goal(
    agent: Any, next_goal: Callable[[], Any], current_goal: str, cycle: int,
) -> tuple[str, str, str, int, int]:
    """Спросить источник о новой цели и вернуть её вместе с ценой вопроса.

    Выбор цели — такой же платный вызов модели, как работа цикла, но он не
    проходит ни через один `CampaignActionOutcome`, и до 18.09 не попадал в
    счёт кампании вовсе. Замер живого прогона
    `trace_f24a00c45aa4a20c77686b069208ba8b`: реестр отдал 13 вызовов, а
    кампания записала 3 — ровно десять обращений за целью прошли мимо книг,
    и `--max-cost-units 300` не видел реально потраченных 195.

    Цена снимается вокруг ВСЕХ попыток, а не вокруг удачной: отказ стоит
    столько же, сколько согласие, а три попытки на смену умножают счёт.
    """
    before_calls, before_cost = _cost_totals(agent)
    candidate = check = act = ""
    for _attempt in range(goal_pick_attempts()):
        try:
            proposed = next_goal()
        except Exception as exc:  # noqa: BLE001 — смена цели не имеет права
            # уронить прогон: не вышло — останавливаемся прежним путём.
            _log(agent, "campaign_goal_switch_failed",
                 {"cycle": cycle, "error": repr(exc)[:200]})
            break
        # Источник целей вправе отдать отчёт (цель + её критерий) или просто
        # строку. Строка означает «критерий не назван» — честное состояние, а
        # не ошибка: так задают цель четыре точки входа.
        asked = str(getattr(proposed, "goal", proposed) or "").strip()
        if asked and asked != current_goal:
            candidate = asked
            check = str(getattr(proposed, "success_check", "") or "").strip()
            act = str(getattr(proposed, "action", "") or "").strip()
            break
    after_calls, after_cost = _cost_totals(agent)
    return (candidate, check, act,
            max(0, after_calls - before_calls), max(0, after_cost - before_cost))


def _wait_for_usd_limit(workspace: Any, ledger: Any, cycle: int, goal: str,
                        now_fn: Any, sleep_fn: Any, pause: float) -> bool:
    """Предел $ в час (план субботы з): превышен — цикл пишет «жду» и спит.

    Предел — `usd_per_hour` в config/budget_limits.json; нет его — False.
    """
    from core.usd_spend import usd_hour_limit, usd_last_hour
    limit = usd_hour_limit(Path(workspace))
    if limit is None:
        return False
    spent = usd_last_hour(Path(workspace))
    if spent < limit:
        return False
    ledger.append(CampaignCycleRecord(
        cycle=cycle, ts=now_fn().isoformat(), goal=goal, action="<usd_limit>",
        action_title="waiting: hourly dollar limit reached", severity="low", priority=0,
        risk="read_only", idle=True, llm_calls_spent=0, cost_units_spent=0, result="waiting",
        reason=f"usd_hour_limit: spent ${spent:.2f} of ${limit:.2f} in the last hour",
        work_done=False, usd_last_hour=spent))
    sleep_fn(max(pause, 60.0))
    return True


def run_campaign(
    config: CampaignConfig,
    *,
    agent: Any,
    workspace: Any,
    approval_inbox: Any = None,
    ledger: CampaignLedger | None = None,
    gather_signals: GatherSignals | None = None,
    execute_action: ExecuteAction | None = None,
    next_goal: Callable[[], str] | None = None,
    opening_spend: tuple[int, int] = (0, 0),
    now_fn: Callable[[], datetime] = _utc_now,
    sleep_fn: Callable[[float], None] = time.sleep,
    on_cycle: Callable[[dict], None] | None = None,
) -> CampaignResult:
    """Run a bounded autonomous campaign and return a full ledgered report.

    The function is blocking and deterministic given its collaborators. It
    NEVER calls a model directly: the only spend happens inside
    ``execute_action`` for a *useful* cycle, and that spend is bounded by
    the campaign budget which is checked BEFORE each cycle.
    """
    gather = gather_signals or _default_gather_signals
    execute = execute_action or _default_execute_action
    if ledger is None:
        ledger = CampaignLedger(path=Path(workspace) / "data" / "campaign_ledger.jsonl")

    records: list[CampaignCycleRecord] = []
    attempted_signatures: set[str] = set()
    refused_signatures: set[str] = set()
    action_steps: dict[str, int] = {}  # WEAVE ред.2 §1: шаги по имени действия
    failed_in_a_row: dict[str, int] = {}  # провалы подряд без работы (_MAX_FAILED_REPEATS)
    # MIR-149: единственная память, переживающая запуски, — леджер; страж
    # повторов слеп к траектории (146 из 150 циклов были циклом №1).
    signature_spend = _cross_run_signature_spend(ledger)
    idle_streak = 0
    #: Текущая цель прогона: она может смениться, поэтому читается отсюда,
    #: а не из замороженного config (право смены — слово оператора 2026-09-01).
    current_goal = config.goal
    #: ...и её критерий успеха, который обязан ехать ВМЕСТЕ с ней. Смена цели
    #: без смены критерия судила бы новую работу по чужой мерке — это хуже
    #: отсутствия мерки: неверная проверка выглядит как проверка.
    current_success_check = config.success_check
    current_goal_action = config.goal_action
    goal_switches = 0
    # Does the current no-progress streak contain repeat cycles? A streak of
    # pure priority-0 observations means the world was checked and found
    # healthy; a streak with repeats means work was wanted and went nowhere.
    # The two deserve different stop reasons (measured 2026-08-13: a healthy
    # dry-run reported «idle_stall», which reads as a failure).
    streak_repeats = False
    previous_goal = ""
    def _switch_goal(cycle: int, why: str) -> bool:
        """Право сменить цель ВНУТРИ прогона (слово оператора 2026-09-01).

        Замер, из-за которого оно понадобилось: узкая цель исчерпывается за
        ОДИН цикл, и кампания умирала через четыре минуты. Смена идёт через
        ТЕ ЖЕ ворота, что и цель на старте: выбирает агент, хартия вправе
        отказать. L5/L6 (2026-09-03): вызывается из ОБОИХ выходов застоя
        (повторы и простой) и получает три попытки, как старт, — одна
        попытка убивала прогон на любом молчании модели.
        """
        nonlocal current_goal, previous_goal, goal_switches, idle_streak, streak_repeats
        nonlocal current_success_check, current_goal_action, llm_calls_used, cost_units_used
        nonlocal goal_started_at
        limit = config.max_goal_switches
        if next_goal is None or (limit and goal_switches >= limit):
            return False
        switched, switched_check, switched_action, spent_calls, spent_cost = _ask_for_a_goal(
            agent, next_goal, current_goal, cycle)
        llm_calls_used += spent_calls
        cost_units_used += spent_cost
        if not switched:
            return False
        goal_switches += 1
        # Уходящая цель судится СВОИМ критерием здесь, а не в конце прогона:
        # судья стоял только на выходе, поэтому за 2685 циклов вердиктов
        # записано три, а 754 цикла «сделано» не проверил никто — «сделано»
        # держалось на слове исполнителя (замер 2026-09-23). Цель закрывается
        # ровно в этой точке, и здесь её критерий ещё в руках.
        judge_closing_goal(agent, workspace=workspace, goal=current_goal,
                           success_check=current_success_check, cycle=cycle, why=why,
                           started_at=goal_started_at, ts=now_fn(),
                           proposals=proposals, artifacts=artifacts)
        previous_goal, current_goal = current_goal, switched
        current_success_check = switched_check
        goal_started_at = now_fn()
        current_goal_action = switched_action
        # Новая цель — новая тема: память о повторах прежней темы не должна
        # объявлять повтором первый же шаг по новой.
        attempted_signatures.clear()
        refused_signatures.clear()
        action_steps.clear()
        failed_in_a_row.clear()
        idle_streak = 0
        streak_repeats = False
        _log(agent, "campaign_goal_switched", {
            "cycle": cycle, "from": previous_goal[:200], "to": current_goal[:200],
            "switches_used": goal_switches, "limit": config.max_goal_switches or "none", "reason": why,
        })
        return True

    # Ревизия PR #346: счёт НЕ начинается с нуля. Первый выбор цели делается
    # хартией в `agent_tick` ДО входа сюда (и при отказе — трижды), поэтому
    # обнулённые счётчики врали кампании ровно на стартовый вызов: замер
    # прогона 18.09 давал 13 вызовов в реестре против 12 в книгах даже после
    # починки смены цели. Кто цель купил, тот её и оплачивает.
    llm_calls_used, cost_units_used = (
        max(0, int(opening_spend[0])), max(0, int(opening_spend[1])),
    )
    proposals = 0
    artifacts = 0
    idle_cycles = 0
    useful_cycles = 0
    goal_drove_cycles = 0
    repeat_cycles = 0
    error_cycles = 0
    consecutive_errors = 0
    unproductive_streak = 0
    unproductive_cycles = 0
    recent_actions: list[str] = []
    clarification: dict[str, Any] | None = None
    stop_reason = ""
    status: CampaignStatus = "completed"
    started_at = now_fn()
    #: Когда началась ТЕКУЩАЯ цель: судья отделяет след, сделанный по ней, от
    #: следа, лежавшего здесь раньше, и без этой отметки чужая работа зачлась
    #: бы новой цели (core/campaign_verdict.against_start).
    goal_started_at = started_at

    def _wait_for_change(cycle: int, stall: str) -> bool:
        """Блок 8: ограниченное ожидание ВНУТРИ процесса вместо смерти.

        Только для прогона с выбором цели (смены): без `next_goal` прежняя
        семантика остановки сохраняется. Просыпается по событию
        (core/wake_events.py: перемена мира, чужая правка кода, слово оператора,
        найм) или новому одобрению; иначе — по периодической перепроверке.
        """
        nonlocal idle_streak, streak_repeats, goal_switches
        if next_goal is None:
            return False
        mark = wake_mark(workspace)
        approved_before = _approved_ids(approval_inbox)
        # Темп ожидания — темп прогона: без паузы между циклами (тесты, ручной
        # запуск) ожидание не спит, а лишь занимает цикл; в смене с паузой 60 с
        # шаг равен паузе, потолок — _BACKOFF_MAX_SECONDS.
        pace = float(config.cycle_pause_seconds or 0)
        limit = min(float(_BACKOFF_MAX_SECONDS), pace * (_BACKOFF_MAX_SECONDS // _BACKOFF_STEP_SECONDS))
        if config.max_wall_clock_seconds:
            remaining = config.max_wall_clock_seconds - (now_fn() - started_at).total_seconds()
            limit = max(0.0, min(limit, remaining))
        waited = 0.0
        woke = ""
        while waited < limit:
            step = min(pace, limit - waited)
            sleep_fn(step)
            waited += step
            woke = woken_by(mark, wake_mark(workspace)) or (
                "new_approval" if _approved_ids(approval_inbox) - approved_before else "")
            if woke:
                break
        record = CampaignCycleRecord(
            cycle=cycle, ts=now_fn().isoformat(), goal=current_goal,
            success_check=current_success_check,
            action="<backoff>", action_title="waiting for the world to change",
            severity="low", priority=0, risk="read_only", idle=True,
            llm_calls_spent=0, cost_units_spent=0, result="waiting",
            reason=f"{stall}; waited {int(waited)}s; woke_by={woke or 'periodic_recheck'}",
            work_done=False,
        )
        ledger.append(record)
        records.append(record)
        _log(agent, "campaign_backoff", record.to_dict())
        _emit_cycle(record)
        # Ожидание — НЕ событие мира. Аудит автономности 2026-09-17 нашёл здесь
        # безусловное обнуление всех счётчиков застоя: двенадцать смен цели,
        # пауза, ещё двенадцать — десять часов перебора тем, и ни один датчик
        # не срабатывал, потому что каждая пауза стирала улики.
        #
        # Право на сброс даёт только НАСТОЯЩАЯ перемена: новая строка в журнале
        # перемен мира или новое одобрение. Тогда прежний вердикт «здесь
        # больше нечего делать» вынесен в другом мире (core/capability_events.py),
        # и новая эра выбора честна. Пустая перепроверка такого права не даёт.
        #
        # `unproductive_streak` не сбрасывается и при перемене мира: это память
        # о том, что исполненные циклы не дали пользы, и она гаснет сама от
        # первого же полезного цикла. Сбросить её здесь значило бы обещать
        # пользу вместо того, чтобы её дождаться.
        if woke:
            idle_streak = 0
            streak_repeats = False
            attempted_signatures.clear()
            refused_signatures.clear()
            action_steps.clear()
            goal_switches = 0
        return True

    def _stall(cycle: int, why: str, stall: str) -> bool:
        """Исход работы упёрся в стену: сменить цель, иначе ждать. False = стоп."""
        return _switch_goal(cycle, why) or _wait_for_change(cycle, stall)

    def _emit_cycle(record: CampaignCycleRecord) -> None:
        if on_cycle is None:
            return
        on_cycle({
            "cycle": record.cycle,
            "result": record.result,
            "idle": record.idle,
            "llm_calls": llm_calls_used,
            "cost_units": cost_units_used,
            "useful_cycles": useful_cycles,
            "goal_drove_cycles": goal_drove_cycles,
            "idle_cycles": idle_cycles,
            "repeat_cycles": repeat_cycles,
            "error_cycles": error_cycles,
        })

    _log(agent, "campaign_start", {
        "goal": config.goal,
        "max_cycles": config.max_cycles,
        "max_llm_calls": config.max_llm_calls,
        "max_cost_units": config.max_cost_units,
        "max_idle_streak": config.max_idle_streak,
        "dry_run": config.dry_run,
    })

    for cycle in range(1, config.max_cycles + 1):
        if cycle > 1 and config.cycle_pause_seconds:
            pause = float(config.cycle_pause_seconds)
            if config.max_wall_clock_seconds:
                remaining = config.max_wall_clock_seconds - (
                    now_fn() - started_at
                ).total_seconds()
                pause = min(pause, max(0.0, remaining))
            if pause > 0:
                sleep_fn(pause)

        if config.max_wall_clock_seconds:
            elapsed = (now_fn() - started_at).total_seconds()
            if elapsed >= config.max_wall_clock_seconds:
                stop_reason = (
                    f"wall_clock_exhausted:{int(elapsed)}s/"
                    f"{config.max_wall_clock_seconds}s"
                )
                status = "stopped"
                break

        if _wait_for_usd_limit(workspace, ledger, cycle, current_goal, now_fn, sleep_fn,
                               float(config.cycle_pause_seconds or 60)):
            continue
        if config.max_llm_calls and llm_calls_used >= config.max_llm_calls:
            stop_reason = f"budget_exhausted:llm_calls={llm_calls_used}/{config.max_llm_calls}"
            status = "stopped"
            break
        if config.max_cost_units and cost_units_used >= config.max_cost_units:
            stop_reason = f"budget_exhausted:cost_units={cost_units_used}/{config.max_cost_units}"
            status = "stopped"
            break

        try:
            # Цель передаётся сборщику сигналов: документная цель обязана быть
            # видима выбирателю действий (живой замер 2026-08-15 — голова
            # выбрала «напиши контракт», руки сделали привычный ремонт).
            # Старые инжектированные сборщики трёх аргументов не ломаются.
            # Исчерпанные потолком предметные действия не должны выигрывать
            # гонку (вердикт агента, CEILING_VERDICT): их имена едут сборщику
            # той же терпимой передачей, что и цель.
            # L2 (2026-09-03): исчерпание сообщают ВСЕ действия, не только четыре
            # предметных: голое действие, повторённое раз, больше не участвует
            # в гонке, и реестр отдаёт следующее дело вместо первого навсегда.
            exhausted_actions = frozenset(
                name for name, steps in action_steps.items()
                if (steps >= _MAX_STEPS_PER_ACTION and name in _SUBJECT_AWARE_ACTIONS)
                or (name not in _SUBJECT_AWARE_ACTIONS and steps >= 1)
            )
            try:
                signals = _call_gather(
                    gather, agent, workspace, approval_inbox,
                    goal=current_goal, goal_is_self=config.goal_is_self,
                    exhausted_actions=exhausted_actions)
            except TypeError:
                signals = gather(agent, workspace, approval_inbox)
            action: BestNextAction = signals["action"]
            if config.goal_first and current_goal and not config.dry_run:
                action = _goal_first(action, attempted_signatures, current_goal_action,
                                     goal_is_self=config.goal_is_self)
            elif (action.priority <= 0 and config.pursue_goal_when_idle and not config.dry_run
                    and current_goal and PURSUE_GOAL not in attempted_signatures):
                action = _pursue_goal_action(action)
            # Любая цель, а не только человеческая: счётчик про то, вела ли
            # работу цель. Чья именно — сказано в основании.
            goal_drove_cycles += int(action.grounds in GOAL_GROUNDS)  # MIR-163
            now = now_fn()

            if action.priority <= 0:
                idle_streak += 1
                idle_cycles += 1
                # Advisory only, for the ledger/operator view — this timestamp is
                # never read back to throttle or skip a cycle. Real pacing is
                # config.cycle_pause_seconds (see the module docstring).
                next_check = (
                    (now + __import__("datetime").timedelta(seconds=config.idle_recheck_seconds)).isoformat()
                    if config.idle_recheck_seconds
                    else None
                )
                record = CampaignCycleRecord(
                    cycle=cycle,
                    ts=now.isoformat(),
                    goal=current_goal,
                    success_check=current_success_check,
                    action=action.action,
                    action_title=action.title,
                    severity=action.severity,
                    priority=action.priority,
                    risk=action.risk,
                    grounds=action.grounds,
                    decided_by=action.decided_by,
                    idle=True,
                    llm_calls_spent=0,
                    cost_units_spent=0,
                    result="idle",
                    reason=action.reason,
                    next_check_at=next_check,
                    work_done=False,
                )
                ledger.append(record)
                records.append(record)
                _log(agent, "campaign_cycle_idle", record.to_dict())
                _emit_cycle(record)
                consecutive_errors = 0

                if idle_streak >= config.max_idle_streak:
                    # L5 (2026-09-03): простой — тоже повод сменить цель, а не
                    # объявить прогон здоровым: цель, которая не связывает
                    # никакой работы, — самый ясный случай для новой цели.
                    if _stall(cycle, f"goal idle: {idle_streak}_checks_found_nothing_to_do",
                              f"idle_stall:{idle_streak}_consecutive_idle_cycles"):
                        continue
                    if streak_repeats:
                        stop_reason = (
                            f"idle_stall:{idle_streak}_consecutive_idle_cycles"
                        )
                        status = "stopped"
                    else:
                        # Good news must not wear a failure's name: every cycle
                        # in the streak observed and found nothing warranting
                        # action, so the campaign is done, not stalled.
                        stop_reason = (
                            f"healthy_idle:{idle_streak}"
                            "_checks_found_nothing_to_do"
                        )
                        status = "completed"
                    break
                continue

            signature = action.action
            subject_aware = signature in _SUBJECT_AWARE_ACTIONS
            hit_ceiling = (
                subject_aware
                and action_steps.get(signature, 0) >= _MAX_STEPS_PER_ACTION
            )
            hit_failures = failed_in_a_row.get(signature, 0) >= _MAX_FAILED_REPEATS
            hit_refusal = signature in refused_signatures
            if hit_ceiling or hit_failures or (not subject_aware and signature in attempted_signatures) or hit_refusal:
                if not subject_aware:
                    action_steps[signature] = action_steps.get(signature, 0) + 1  # L2
                idle_streak += 1
                streak_repeats = True
                repeat_cycles += 1
                record = CampaignCycleRecord(
                    cycle=cycle,
                    ts=now.isoformat(),
                    goal=current_goal,
                    success_check=current_success_check,
                    action=action.action,
                    action_title=action.title,
                    severity=action.severity,
                    priority=action.priority,
                    risk=action.risk,
                    grounds=action.grounds,
                    decided_by=action.decided_by,
                    idle=False,
                    llm_calls_spent=0,
                    cost_units_spent=0,
                    result="repeat",
                    reason=(
                        _refusal_reason(action.action) if hit_refusal
                        else _repeat_reason(
                            action.action, hit_ceiling,
                            failed_in_a_row.get(signature, 0) if hit_failures else 0)
                    ),
                    work_done=False,
                )
                ledger.append(record)
                records.append(record)
                _log(agent, "campaign_cycle_repeat", record.to_dict())
                _emit_cycle(record)
                consecutive_errors = 0

                if idle_streak >= config.max_idle_streak:
                    if _stall(cycle, "goal exhausted: "
                              f"{config.max_idle_streak}_cycles_without_new_action",
                              f"no_progress_stall:{idle_streak}_cycles_without_new_action"):
                        continue
                    stop_reason = f"no_progress_stall:{idle_streak}_cycles_without_new_action"
                    status = "stopped"
                    break
                continue

            spent_before = signature_spend.get(signature, 0)
            if (config.max_cost_units_per_signature
                    and spent_before >= config.max_cost_units_per_signature):
                record = _cost_cap_record(
                    cycle=cycle, ts=now.isoformat(), goal=current_goal,
                    success_check=current_success_check,
                    action=action, spent=spent_before,
                    cap=config.max_cost_units_per_signature)
                ledger.append(record)
                records.append(record)
                _log(agent, "campaign_cost_cap", record.to_dict())
                # Его след бюджетной стены; reason по его правилу
                # «действие + факт упора», без изменчивых чисел.
                _stop = record_self_stop(
                    kind="budget_stop",
                    source="data/campaign_ledger.jsonl",
                    reason=f"{action.action}:cap",
                    ts=now.isoformat(),
                    outcome="cost_cap",
                    workspace=workspace,
                )
                record_stop_observation(
                    workspace,
                    kind="budget_stop",
                    reason=f"{action.action}:cap",
                    signature=str(_stop.get("signature") or ""),
                    source="data/campaign_ledger.jsonl",
                )
                _emit_cycle(record)
                consecutive_errors = 0
                idle_streak += 1
                streak_repeats = True
                if idle_streak >= config.max_idle_streak:
                    if _stall(cycle, "cost cap: awaiting the operator's word",
                              f"cost_cap_stall:{idle_streak}_cycles_awaiting_operator"):
                        continue
                    stop_reason = f"cost_cap_stall:{idle_streak}_cycles_awaiting_operator"
                    status = "stopped"
                    break
                continue

            streak_repeats, effects_before = False, _effects_count(agent)
            with _cycle_cost_envelope(agent, config, cost_units_used):
                outcome = execute(
                    agent=agent,
                    workspace=workspace,
                    action=action,
                    # L1 (2026-09-03): исполнитель слышит ТЕКУЩУЮ цель — после
                    # смены ему уходила замороженная стартовая. Вместе с целью
                    # едет её критерий успеха: судить выполнение по мерке
                    # прежней цели — не проверка, а её видимость.
                    config=(
                        replace(config, goal=current_goal,
                                success_check=current_success_check)
                        if (current_goal != config.goal
                            or current_success_check != config.success_check)
                        else config
                    ),
                    approval_inbox=approval_inbox,
                )
            llm_calls_used += max(0, outcome.llm_calls_spent)
            cost_units_used += max(0, outcome.cost_units_spent)
            proposals += int(outcome.proposal is not None)
            artifacts += int(outcome.artifact is not None)

            # MIR-117: подпись банится попыткой, полезность — работой (см. типы).
            if outcome.ran:
                stalled = _bank_signature(
                    agent, signature, outcome, subject_aware,
                    attempted_signatures, action_steps,
                )
                idle_streak = (idle_streak + 1) * int(stalled)
                streak_repeats = streak_repeats or stalled
            # Отказ на воротах запоминается ОТДЕЛЬНО от попыток: сам он
            # попыткой не является, но «этот путь сейчас закрыт» — факт, и
            # без него следующий цикл предлагал то же самое (170 подряд,
            # замер 2026-09-19).
            _refused_again(signature, refused_signatures,
                           ran=outcome.ran, result=str(outcome.result))
            _reopen_operator_goal(action, config, agent, effects_before, attempted_signatures)
            if outcome.did_work:
                useful_cycles += 1
            failed_in_a_row[signature] = (
                failed_in_a_row.get(signature, 0) + 1
                if not outcome.did_work and not outcome.ran else 0
            )
            # MIR-149: межзапусковая память цены пополняется и внутри запуска.
            signature_spend[signature] = spent_before + max(0, outcome.cost_units_spent)

            record = CampaignCycleRecord(
                cycle=cycle,
                ts=now.isoformat(),
                goal=current_goal,
                success_check=current_success_check,
                action=action.action,
                action_title=action.title,
                severity=action.severity,
                priority=action.priority,
                risk=action.risk,
                grounds=action.grounds,
                decided_by=action.decided_by,
                idle=False,
                llm_calls_spent=max(0, outcome.llm_calls_spent),
                cost_units_spent=max(0, outcome.cost_units_spent),
                result=outcome.result,
                reason=action.reason,
                proposal=outcome.proposal,
                artifact=outcome.artifact,
                work_done=outcome.did_work,
            )
            ledger.append(record)
            records.append(record)
            _log(agent, "campaign_cycle_work", record.to_dict())
            _emit_cycle(record)
            consecutive_errors = 0
            # Цель судится после каждого рабочего цикла (goal_met_now).
            met = outcome.did_work and goal_met_now(current_goal, current_success_check,
                                                    workspace, goal_started_at, _verifier_llm(agent))
            if met and _switch_goal(cycle, "goal verified after this cycle"):
                continue
            if idle_streak >= config.max_idle_streak:
                if _stall(cycle, f"goal exhausted: {idle_streak}_cycles_without_new_action",
                          f"no_progress_stall:{idle_streak}_cycles_without_new_action"):
                    continue
                stop_reason = f"no_progress_stall:{idle_streak}_cycles_without_new_action"
                status = "stopped"
                break

            recent_actions.append(action.action)
            # Продуктивность = видимый ПРОДУКТ, не занятость; нога «строка
            # сменилась» удалена (MIR-117) — смена слова не прогресс.
            productive = outcome.artifact is not None or outcome.proposal is not None
            if productive:
                unproductive_streak = 0
            else:
                unproductive_streak += 1
                unproductive_cycles += 1
                if (
                    config.max_unproductive_streak
                    and unproductive_streak >= config.max_unproductive_streak
                ):
                    suspected = (
                        f"loop_suspected:"
                        f"{unproductive_streak}_cycles_without_useful_change"
                    )
                    from core.clarification_gate import for_loop_suspected
                    suspected_clarification = for_loop_suspected().to_dict()
                    _log(agent, "campaign_loop_suspected", {
                        "cycles_without_progress": unproductive_streak,
                        "recent_actions": recent_actions[-5:],
                        "llm_calls_spent": llm_calls_used,
                        "cost_units_spent": cost_units_used,
                        "useful_state_change": False,
                        "recommended_action": "enter_clarify_mode",
                        "clarification": suspected_clarification,
                        "reason": (
                            "no new artifact, proposal, or result-status change "
                            "across the last "
                            f"{unproductive_streak} executed cycles"
                        ),
                    })
                    # Блок 8: третий выход, который блок 2 не тронул, — вечер
                    # 2026-09-03 закончился здесь на 21-м цикле после трёх
                    # честных «невыразимо». Петля — исход работы, не конец смены.
                    if _stall(cycle, f"loop suspected: {unproductive_streak}_cycles_without_useful_change",
                              suspected):
                        continue
                    stop_reason = suspected
                    status = "stopped"
                    clarification = suspected_clarification
                    break
        except Exception as exc:  # noqa: BLE001 — per-cycle resilience seam
            consecutive_errors += 1
            error_cycles += 1
            err_now = now_fn()
            err_record = CampaignCycleRecord(
                cycle=cycle,
                ts=err_now.isoformat(),
                goal=current_goal,
                success_check=current_success_check,
                action="<cycle_error>",
                action_title="cycle raised an exception",
                severity="error",
                priority=0,
                risk="unknown",
                idle=False,
                llm_calls_spent=0,
                cost_units_spent=0,
                result="error",
                reason=f"{type(exc).__name__}: {exc}",
            )
            records.append(err_record)
            try:
                ledger.append(err_record)
            except Exception:  # noqa: BLE001, S110 — an error record that cannot be filed must not replace the error
                pass
            try:
                _log(agent, "campaign_cycle_error", {
                    "cycle": cycle,
                    "error": f"{type(exc).__name__}: {exc}",
                    "consecutive_errors": consecutive_errors,
                    "max_consecutive_errors": config.max_consecutive_errors,
                })
            except Exception:  # noqa: BLE001, S110 — an error record that cannot be filed must not replace the error
                pass
            try:
                _emit_cycle(err_record)
            except Exception:  # noqa: BLE001, S110 — an error record that cannot be filed must not replace the error
                pass
            if consecutive_errors >= config.max_consecutive_errors:
                stop_reason = (
                    f"error_stall:{consecutive_errors}_consecutive_cycle_errors"
                )
                status = "stopped"
                break
            continue

    if not stop_reason:
        # Блок 8: потолок циклов — часть лимита смены, не шестой тип
        # остановки (ратифицированные терминальные классы: бюджет, safety/
        # authority, повреждённое состояние, инфраструктура, конец смены).
        stop_reason = f"shift_limit:max_cycles={config.max_cycles}"
    totals = {
        "llm_calls": llm_calls_used,
        "cost_units": cost_units_used,
        "idle_cycles": idle_cycles,
        "useful_cycles": useful_cycles,
        "repeat_cycles": repeat_cycles,
        "error_cycles": error_cycles,
        "unproductive_cycles": unproductive_cycles,
        # Считалось с MIR-163, в итог не попадало: сводка печатала умолчание.
        "goal_drove_cycles": goal_drove_cycles,
        "proposals": proposals,
        "artifacts": artifacts,
        "wall_clock_seconds": round((now_fn() - started_at).total_seconds(), 1),
    }
    result = CampaignResult(
        status=status,
        goal=current_goal,
        stop_reason=stop_reason,
        cycles_run=len(records),
        records=records,
        totals=totals,
        clarification=clarification,
    )
    # Кампания судит СВОЮ цель её же критерием — впервые с появления
    # `success_check`. Судья читает мир, а не слово исполнителя, и отделяет
    # след, сделанный этим прогоном, от следа, лежавшего здесь до него
    # (см. core/campaign_verdict.py: замер 4 ложных «сошлось» из 31).
    verdict, verdict_error = judge_and_record(
        goal=current_goal, success_check=current_success_check,
        workspace=workspace, started_at=started_at, ts=now_fn(),
        stop_reason=stop_reason, cycles_run=len(records),
        proposals=proposals, artifacts=artifacts, llm=_verifier_llm(agent),
    )
    result.success_verdict = verdict
    if verdict_error:
        _log(agent, "campaign_verdict_unrecorded", {"error": verdict_error})
    _log(agent, "campaign_stop", {
        "status": result.status,
        "goal": result.goal,
        "stop_reason": result.stop_reason,
        "cycles_run": result.cycles_run,
        "totals": totals,
        "success_verdict": verdict,
    })
    return result
