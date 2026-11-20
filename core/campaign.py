"""Bounded 24/48h autonomous campaign loop: pure loop plus injectable signal-gathering and action-execution collaborators."""
from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta
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

# Static allow-list of subject-aware actions, known before execution.
_SUBJECT_AWARE_ACTIONS = frozenset({
    "explain_causal_observation",
    "discriminate_causal_claim",
    "run_claim_experiment",
    "birth_experiment_specs",
})
_MAX_STEPS_PER_ACTION = 10
#: Consecutive empty outcomes (no work, no spend) of one action after which it is skipped.
_MAX_FAILED_REPEATS = 3

CampaignStatus = Literal["completed", "stopped"]


def _session_cost_units(agent: Any) -> int | None:
    """The session cost counter the run envelope bounds, or None without a usage ledger."""
    try:
        ledger = getattr(agent.model_router, "usage_ledger", None)
        if ledger is None:
            return None
        return int(ledger.session_cost_units())
    except (AttributeError, TypeError, ValueError):
        return None


def _cycle_cost_envelope(agent: Any, config: CampaignConfig, cost_units_used: int):
    """One useful cycle's spend bound as a run cost envelope (nullcontext without a cap or ledger)."""
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
    """Spend per signature across all earlier runs; empty memory without a file."""
    if ledger is None or ledger.path is None:
        return {}
    try:
        return spent_units_by_action(load_ledger_rows(ledger.path))
    except OSError:
        return {}


def _cost_cap_record(*, cycle: int, ts: str, goal: str, action: BestNextAction,
                     spent: int, cap: int,
                     success_check: str = "") -> CampaignCycleRecord:
    """Escalation row for a fallen cost cap: execution becomes a question for the operator.

    The cap is a budget policy, not a derived discriminator; zero-cost (observe) signatures never reach here.
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
    """Verifier model for the content judge (core/goal_content_judge.py), or None."""
    try:
        return agent.model_router.for_role("verifier")
    except Exception:  # noqa: BLE001 — no router: judge by files
        return None


def goal_met_now(goal: str, success_check: str, workspace: Any, started_at: Any, llm: Any = None) -> bool:
    """Whether the goal criterion holds on fresh evidence, checked after EVERY working cycle;
    with `llm` the content judge may read the fresh product through the model;
    no verdict is written here.
    """
    if not str(success_check or "").strip():
        return False
    try:
        since = started_at.timestamp()
        verdict = judge_campaign(goal=goal, success_check=success_check,
                                 workspace=workspace, since=since, llm=llm)
    except Exception:  # noqa: BLE001 — the judge must not fail the cycle
        return False
    return verdict.get("verdict") == "verified"


def judge_closing_goal(agent: Any, *, workspace: Any, goal: str, success_check: str,
                       cycle: int, why: str, started_at: Any, ts: Any,
                       proposals: int = 0, artifacts: int = 0) -> dict[str, Any] | None:
    """Judge a closing goal by its own criterion and record the testimony; a goal without a criterion is not judged."""
    if not str(success_check or "").strip():
        return None
    try:
        verdict, error = judge_and_record(
            goal=goal, success_check=success_check, workspace=workspace,
            started_at=started_at, ts=ts, stop_reason=why, cycles_run=cycle,
            proposals=proposals, artifacts=artifacts, llm=_verifier_llm(agent),
        )
    except Exception as exc:  # noqa: BLE001 — testimony must not fail the run
        _log(agent, "campaign_goal_verdict_failed",
             {"cycle": cycle, "error": repr(exc)[:200]})
        return None
    _log(agent, "campaign_goal_verdict", {
        "cycle": cycle, "goal": str(goal)[:200], "verdict": verdict.get("verdict"),
        "reason": str(verdict.get("reason") or "")[:200], "unrecorded": error,
    })
    return verdict


def _approved_ids(approval_inbox) -> frozenset[str]:
    """Approved item ids, or empty when the inbox is absent or unreadable."""
    if approval_inbox is None:
        return frozenset()
    try:
        return frozenset(str(i.id) for i in approval_inbox.list(status="approved"))
    except Exception:  # noqa: BLE001 — a reading hiccup must not fake a wake or a stop
        return frozenset()


#: Action 'the goal itself': one agent pass on its chosen goal when the menu offers nothing allowed.
PURSUE_GOAL = "pursue_goal"


def _pursue_goal_action(idle: BestNextAction, reason: str = "") -> BestNextAction:
    """Turn an idle cycle into work on the goal."""
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


#: Passes without a record that the operator's goal gets before an idle stop.
OPERATOR_GOAL_EMPTY_PASSES = 3
_EMPTY_ROUND_MARK = "~operator_goal_empty_pass_"


def _reopen_operator_goal(action: BestNextAction, config: CampaignConfig, agent: Any,
                          effects_before: int, attempted: set[str]) -> None:
    """Give the operator's goal a new pass; stop after three consecutive passes without a record."""
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
    """Goal-first mode (CampaignConfig.goal_first): a breakage takes the menu, otherwise the goal.

    `goal_is_self` only names the goal's origin and decides nothing.
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
    """Call the signal gatherer with ONLY the arguments it accepts (signature inspected once)."""
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
    """Wording for a refusal at the gate, distinct from the retry wording."""
    return (f"'{action_name}': ворота отказали до старта, и разрешение с тех пор "
            "не появилось — повторное предложение ничего не изменит")


def _refused_again(signature: str, refused: set[str], *, ran: bool, result: str) -> bool:
    """True if the action was already refused at the gate in this series of cycles.

    A gate refusal is not an attempt, but it is a separate fact (the path is closed now), remembered separately.
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


#: Max goal switches per run; too many means the run should end rather than cycle topics.
_MAX_GOAL_SWITCHES = 12
#: Goal-pick attempts per switch, same as at start (agent_tick._GOAL_PICK_ATTEMPTS).
_GOAL_SWITCH_ATTEMPTS = 3


def goal_pick_attempts() -> int:
    """Goal-pick attempts at start and on switch; `AGENT_GOAL_PICK_ATTEMPTS`, default 3, cap 20."""
    import os

    try:
        n = int(os.environ.get("AGENT_GOAL_PICK_ATTEMPTS") or _GOAL_SWITCH_ATTEMPTS)
    except ValueError:
        n = _GOAL_SWITCH_ATTEMPTS
    return max(1, min(20, n))
#: In-shift waiting: wake on the world-change log (data/capability_events.jsonl) or a new approval; the periodic recheck insures against a missed event.
_BACKOFF_STEP_SECONDS = 60
_BACKOFF_MAX_SECONDS = 900


def _ask_for_a_goal(
    agent: Any, next_goal: Callable[[], Any], current_goal: str, cycle: int,
) -> tuple[str, str, str, int, int]:
    """Ask the source for a new goal and return it with the cost of asking (all attempts, refusals included, are priced)."""
    before_calls, before_cost = _cost_totals(agent)
    candidate = check = act = ""
    for _attempt in range(goal_pick_attempts()):
        try:
            proposed = next_goal()
        except Exception as exc:  # noqa: BLE001 — a goal switch must not crash the run
            _log(agent, "campaign_goal_switch_failed",
                 {"cycle": cycle, "error": repr(exc)[:200]})
            break
        # A goal source may return a report (goal + criterion) or a plain string; a string means 'no criterion named'.
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
                        now_fn: Any, sleep_fn: Any, pause: float) -> bool | str:
    """Hourly $ limit (`usd_per_hour` in config/budget_limits.json): when exceeded the cycle logs 'waiting' and sleeps.

    No file means False; an unreadable file yields a stop reason.
    """
    from core.usd_spend import unpriced_note, usd_hour_limit, usd_since
    since = _utc_now() - timedelta(hours=1)

    def row(title: str, result: str, reason: str, spent: float | None) -> CampaignCycleRecord:
        return CampaignCycleRecord(
            cycle=cycle, ts=now_fn().isoformat(), goal=goal, action="<usd_limit>",
            action_title=title, severity="low", priority=0, risk="read_only", idle=True,
            llm_calls_spent=0, cost_units_spent=0, result=result, reason=reason,
            work_done=False, usd_last_hour=spent)

    try:
        limit = usd_hour_limit(Path(workspace))
        if limit is None:
            return False
        spent = usd_since(Path(workspace), since)
        if spent < limit:
            return False
        note = unpriced_note(Path(workspace), since)
    except ValueError as exc:
        stop = f"hourly dollar limit cannot be checked: {exc}"
        try:
            ledger.append(row("stopped: hourly dollar limit cannot be checked", "stopped", stop, None))
        except ValueError:
            pass  # the ledger failed on the same read; the reason goes into the result
        return stop
    ledger.append(row(
        "waiting: hourly dollar limit reached", "waiting",
        f"usd_hour_limit: spent ${spent:.2f} of ${limit:.2f} in the last hour{note}", spent))
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
    """Run a bounded autonomous campaign and return a full ledgered report; it calls no model
    itself (models run in `execute_action`, `next_goal` and the verifier-backed goal judge)."""
    gather = gather_signals or _default_gather_signals
    execute = execute_action or _default_execute_action
    if ledger is None:
        ledger = CampaignLedger(path=Path(workspace) / "data" / "campaign_ledger.jsonl")

    records: list[CampaignCycleRecord] = []
    attempted_signatures: set[str] = set()
    refused_signatures: set[str] = set()
    action_steps: dict[str, int] = {}  # steps per action name
    failed_in_a_row: dict[str, int] = {}  # consecutive failures without work (_MAX_FAILED_REPEATS)
    # The only cross-run memory is the ledger; the repeat guard is blind to trajectory.
    signature_spend = _cross_run_signature_spend(ledger)
    idle_streak = 0
    #: Current goal; it can switch mid-run, so read it from here rather than the frozen config.
    current_goal = config.goal
    #: Its success criterion travels WITH it: judging new work by the old goal's yardstick is worse than none.
    current_success_check = config.success_check
    current_goal_action = config.goal_action
    goal_switches = 0
    # Does the no-progress streak contain repeat cycles? Pure observations stop as healthy_idle, repeats as a stall.
    streak_repeats = False
    previous_goal = ""
    def _switch_goal(cycle: int, why: str) -> bool:
        """Switch goal inside the run through the same gate as the start goal (agent chooses, charter may refuse), with three attempts."""
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
        # The leaving goal is judged by its own criterion here, while it is still in hand.
        judge_closing_goal(agent, workspace=workspace, goal=current_goal,
                           success_check=current_success_check, cycle=cycle, why=why,
                           started_at=goal_started_at, ts=now_fn(),
                           proposals=proposals, artifacts=artifacts)
        previous_goal, current_goal = current_goal, switched
        current_success_check = switched_check
        goal_started_at = now_fn()
        current_goal_action = switched_action
        # New goal, new topic: old repeat memory must not flag its first step.
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

    # Counters start from the goal pick already paid for by agent_tick before this call.
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
    #: When the CURRENT goal started; lets the judge ignore earlier work (core/campaign_verdict.against_start).
    goal_started_at = started_at

    def _wait_for_change(cycle: int, stall: str) -> bool:
        """Bounded in-process wait instead of dying; only for runs with goal switching (`next_goal`).

        Wakes on an event (core/wake_events.py) or a new approval, else on periodic recheck.
        """
        nonlocal idle_streak, streak_repeats, goal_switches
        if next_goal is None:
            return False
        mark = wake_mark(workspace)
        approved_before = _approved_ids(approval_inbox)
        # Waiting pace follows the run's pace: no pause means no sleep; step = pause, capped by _BACKOFF_MAX_SECONDS.
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
        # Waiting is NOT a world event: only a new capability-events row or a new approval resets stall counters.
        # `unproductive_streak` is never reset here; it clears on the first useful cycle.
        if woke:
            idle_streak = 0
            streak_repeats = False
            attempted_signatures.clear()
            refused_signatures.clear()
            action_steps.clear()
            goal_switches = 0
        return True

    def _stall(cycle: int, why: str, stall: str) -> bool:
        """Work hit a wall: switch goal, else wait. False = stop."""
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

        if (usd_stop := _wait_for_usd_limit(workspace, ledger, cycle, current_goal, now_fn,
                                            sleep_fn, float(config.cycle_pause_seconds or 60))) is True:
            continue
        if usd_stop or (config.max_llm_calls and llm_calls_used >= config.max_llm_calls):
            stop_reason = usd_stop or f"budget_exhausted:llm_calls={llm_calls_used}/{config.max_llm_calls}"
            status = "stopped"
            break
        if config.max_cost_units and cost_units_used >= config.max_cost_units:
            stop_reason = f"budget_exhausted:cost_units={cost_units_used}/{config.max_cost_units}"
            status = "stopped"
            break

        try:
            # Goal and exhausted action names (all actions, not only subject-aware ones) go to the gatherer tolerantly.
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
            # Counts cycles driven by any goal, not only the operator's; the grounds say whose.
            goal_drove_cycles += int(action.grounds in GOAL_GROUNDS)
            now = now_fn()

            if action.priority <= 0:
                idle_streak += 1
                idle_cycles += 1
                # Advisory only (ledger/operator view); never read back to pace cycles.
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
                    # An idle goal is also a reason to switch goal rather than declare the run healthy.
                    if _stall(cycle, f"goal idle: {idle_streak}_checks_found_nothing_to_do",
                              f"idle_stall:{idle_streak}_consecutive_idle_cycles"):
                        continue
                    if streak_repeats or (not config.goal_is_self
                                          and PURSUE_GOAL in attempted_signatures):
                        stop_reason = f"idle_stall:{idle_streak}_consecutive_idle_cycles"
                        status = "stopped"
                    else:
                        # Good news must not wear a failure's name: all cycles observed, nothing to do, so done, not stalled.
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
                    action_steps[signature] = action_steps.get(signature, 0) + 1
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
                # Budget-wall trace; reason is 'action + fact of hitting the wall', no volatile numbers.
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
                    # The executor hears the CURRENT goal and its success criterion, not the frozen start goal.
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

            # A signature is banned by an attempt; usefulness is earned by work.
            if outcome.ran:
                stalled = _bank_signature(
                    agent, signature, outcome, subject_aware,
                    attempted_signatures, action_steps,
                )
                idle_streak = (idle_streak + 1) * int(stalled)
                streak_repeats = streak_repeats or stalled
            # A gate refusal is remembered separately from attempts: not an attempt, but the path is closed.
            _refused_again(signature, refused_signatures,
                           ran=outcome.ran, result=str(outcome.result))
            _reopen_operator_goal(action, config, agent, effects_before, attempted_signatures)
            if outcome.did_work:
                useful_cycles += 1
            failed_in_a_row[signature] = (
                failed_in_a_row.get(signature, 0) + 1
                if not outcome.did_work and not outcome.ran else 0
            )
            # Cross-run cost memory is also topped up within the run.
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
            # The goal is judged after every working cycle (goal_met_now).
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
            # Productive = a visible product, not busyness.
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
                    # A suspected loop is a work outcome, not the end of the shift.
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
        # The cycle ceiling is part of the shift limit, not a sixth stop type.
        stop_reason = f"shift_limit:max_cycles={config.max_cycles}"
    totals = {
        "llm_calls": llm_calls_used,
        "cost_units": cost_units_used,
        "idle_cycles": idle_cycles,
        "useful_cycles": useful_cycles,
        "repeat_cycles": repeat_cycles,
        "error_cycles": error_cycles,
        "unproductive_cycles": unproductive_cycles,
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
    # The campaign judges its OWN goal by its criterion, reading the world, not the executor's word (core/campaign_verdict.py).
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
