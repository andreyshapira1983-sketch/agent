"""24/48h autonomous work campaign engine.

A *campaign* is the layer above a single tick. A tick does one thing; a
campaign answers ONE honest question across many cycles:

    "For these keys, did the agent move the system forward — or just burn
     tokens?"

The loop is::

    goal -> budget -> cycle -> best_next_action
                              |
                  +-----------+-----------+
                  |                       |
            no high-priority         a real action
            action (observe)              |
                  |                        |
            IDLE  (NO LLM)          one bounded GATED pass
            record reason_if_idle    (existing approval gate)
            advance next_check_at    record cost + result + proposal
                  |                        |
                  +-----------+-----------+
                              |
                       campaign ledger
                              |
                  stop on: budget | 3 idle in a row | max cycles

Hard guarantees (the whole point of this layer):

* An IDLE cycle never calls the LLM. When there is no high-priority action the
  agent records ``reason_if_idle`` and advances ``next_check_at`` instead of
  asking a model "what should I do" (which would cost money to be told
  "nothing").
* ``max_idle_streak`` consecutive idle cycles stops the campaign with a report
  ("3 empty cycles -> stop and ask the operator").
* The campaign opens NO new effect path. A useful cycle runs through the
  existing :class:`~core.autonomous_runtime.AutonomousRuntime`, which already
  routes every effect through PolicyGate + the approval inbox. Dry-run is the
  default.
* Budget caps (cycles / llm_calls / cost_units) stop the campaign BEFORE the
  next spend, not after.

This module is deliberately split into a *pure loop* (``run_campaign``) plus two
injectable collaborators (``gather_signals`` and ``execute_action``). The
defaults wire the real signal-gathering and the real bounded runtime pass; tests
inject deterministic fakes and assert on the real record shapes.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from core.best_next_action import BestNextAction
from core.campaign_io import (
    _action_focused_goal,
    _cost_totals,
    _default_execute_action,
    _default_gather_signals,
    _log,
)
from core.campaign_ledger import (
    CampaignCycleRecord,
    CampaignLedger,
    _format_ledger_row,
    load_ledger_rows,
    summarise_ledger,
)
from core.campaign_types import CampaignActionOutcome, CampaignConfig, CampaignResult


CampaignStatus = Literal["completed", "stopped"]


def _utc_now() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)


GatherSignals = Callable[[Any, Any, Any], dict[str, Any]]
ExecuteAction = Callable[..., CampaignActionOutcome]


def run_campaign(
    config: CampaignConfig,
    *,
    agent: Any,
    workspace: Any,
    approval_inbox: Any = None,
    ledger: Optional[CampaignLedger] = None,
    gather_signals: Optional[GatherSignals] = None,
    execute_action: Optional[ExecuteAction] = None,
    now_fn: Callable[[], datetime] = _utc_now,
    sleep_fn: Callable[[float], None] = time.sleep,
    on_cycle: Optional[Callable[[dict], None]] = None,
) -> CampaignResult:
    """Run a bounded autonomous campaign and return a full ledgered report.

    The function is blocking and deterministic given its collaborators. It
    NEVER calls a model directly: the only spend happens inside
    ``execute_action`` for a *useful* cycle, and that spend is bounded by the
    campaign budget which is checked BEFORE each cycle.

    Real wall-clock pacing (``cycle_pause_seconds`` / ``max_wall_clock_seconds``)
    is driven through the injected ``now_fn`` and ``sleep_fn`` so tests stay
    instant and deterministic. With both at their ``0`` defaults the loop runs
    cycles back-to-back exactly as before.

    ``on_cycle`` is an optional liveness seam fired once per recorded cycle with
    a small snapshot dict (cycle number, result, running totals). It lets a
    daemon emit a heartbeat per cycle during a multi-hour paced run WITHOUT the
    pure loop knowing anything about heartbeats/I/O. Default ``None`` = no-op.
    """
    gather = gather_signals or _default_gather_signals
    execute = execute_action or _default_execute_action
    if ledger is None:
        ledger = CampaignLedger(path=Path(workspace) / "data" / "campaign_ledger.jsonl")

    records: list[CampaignCycleRecord] = []
    attempted_signatures: set[str] = set()
    idle_streak = 0
    llm_calls_used = 0
    cost_units_used = 0
    proposals = 0
    artifacts = 0
    idle_cycles = 0
    useful_cycles = 0
    repeat_cycles = 0
    error_cycles = 0
    consecutive_errors = 0
    unproductive_streak = 0
    unproductive_cycles = 0
    prev_exec_result: Optional[str] = None
    recent_actions: list[str] = []
    clarification: Optional[dict[str, Any]] = None
    stop_reason = ""
    status: CampaignStatus = "completed"
    started_at = now_fn()

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

        if config.max_llm_calls and llm_calls_used >= config.max_llm_calls:
            stop_reason = f"budget_exhausted:llm_calls={llm_calls_used}/{config.max_llm_calls}"
            status = "stopped"
            break
        if config.max_cost_units and cost_units_used >= config.max_cost_units:
            stop_reason = f"budget_exhausted:cost_units={cost_units_used}/{config.max_cost_units}"
            status = "stopped"
            break

        try:
            signals = gather(agent, workspace, approval_inbox)
            action: BestNextAction = signals["action"]
            now = now_fn()

            if action.priority <= 0:
                idle_streak += 1
                idle_cycles += 1
                next_check = (
                    (now + __import__("datetime").timedelta(seconds=config.idle_recheck_seconds)).isoformat()
                    if config.idle_recheck_seconds
                    else None
                )
                record = CampaignCycleRecord(
                    cycle=cycle,
                    ts=now.isoformat(),
                    goal=config.goal,
                    action=action.action,
                    action_title=action.title,
                    severity=action.severity,
                    priority=action.priority,
                    risk=action.risk,
                    idle=True,
                    llm_calls_spent=0,
                    cost_units_spent=0,
                    result="idle",
                    reason=action.reason,
                    next_check_at=next_check,
                )
                ledger.append(record)
                records.append(record)
                _log(agent, "campaign_cycle_idle", record.to_dict())
                _emit_cycle(record)
                consecutive_errors = 0

                if idle_streak >= config.max_idle_streak:
                    stop_reason = f"idle_stall:{idle_streak}_consecutive_idle_cycles"
                    status = "stopped"
                    break
                continue

            signature = action.action
            if signature in attempted_signatures:
                idle_streak += 1
                repeat_cycles += 1
                record = CampaignCycleRecord(
                    cycle=cycle,
                    ts=now.isoformat(),
                    goal=config.goal,
                    action=action.action,
                    action_title=action.title,
                    severity=action.severity,
                    priority=action.priority,
                    risk=action.risk,
                    idle=False,
                    llm_calls_spent=0,
                    cost_units_spent=0,
                    result="repeat",
                    reason=(
                        f"already attempted '{action.action}' this campaign; the "
                        f"earlier pass did not clear the signal — skipping re-execution"
                    ),
                )
                ledger.append(record)
                records.append(record)
                _log(agent, "campaign_cycle_repeat", record.to_dict())
                _emit_cycle(record)
                consecutive_errors = 0

                if idle_streak >= config.max_idle_streak:
                    stop_reason = f"no_progress_stall:{idle_streak}_cycles_without_new_action"
                    status = "stopped"
                    break
                continue

            idle_streak = 0
            attempted_signatures.add(signature)
            useful_cycles += 1
            outcome = execute(
                agent=agent,
                workspace=workspace,
                action=action,
                config=config,
                approval_inbox=approval_inbox,
            )
            llm_calls_used += max(0, outcome.llm_calls_spent)
            cost_units_used += max(0, outcome.cost_units_spent)
            if outcome.proposal:
                proposals += 1
            if outcome.artifact:
                artifacts += 1

            record = CampaignCycleRecord(
                cycle=cycle,
                ts=now.isoformat(),
                goal=config.goal,
                action=action.action,
                action_title=action.title,
                severity=action.severity,
                priority=action.priority,
                risk=action.risk,
                idle=False,
                llm_calls_spent=max(0, outcome.llm_calls_spent),
                cost_units_spent=max(0, outcome.cost_units_spent),
                result=outcome.result,
                reason=action.reason,
                proposal=outcome.proposal,
                artifact=outcome.artifact,
            )
            ledger.append(record)
            records.append(record)
            _log(agent, "campaign_cycle_work", record.to_dict())
            _emit_cycle(record)
            consecutive_errors = 0

            recent_actions.append(action.action)
            productive = (
                outcome.artifact is not None
                or outcome.proposal is not None
                or (prev_exec_result is not None and outcome.result != prev_exec_result)
            )
            prev_exec_result = outcome.result
            if productive:
                unproductive_streak = 0
            else:
                unproductive_streak += 1
                unproductive_cycles += 1
                if (
                    config.max_unproductive_streak
                    and unproductive_streak >= config.max_unproductive_streak
                ):
                    stop_reason = (
                        f"loop_suspected:"
                        f"{unproductive_streak}_cycles_without_useful_change"
                    )
                    status = "stopped"
                    from core.clarification_gate import for_loop_suspected
                    clarification = for_loop_suspected().to_dict()
                    _log(agent, "campaign_loop_suspected", {
                        "cycles_without_progress": unproductive_streak,
                        "recent_actions": recent_actions[-5:],
                        "llm_calls_spent": llm_calls_used,
                        "cost_units_spent": cost_units_used,
                        "useful_state_change": False,
                        "recommended_action": "enter_clarify_mode",
                        "clarification": clarification,
                        "reason": (
                            "no new artifact, proposal, or result-status change "
                            "across the last "
                            f"{unproductive_streak} executed cycles"
                        ),
                    })
                    break
        except Exception as exc:  # noqa: BLE001 — per-cycle resilience seam
            consecutive_errors += 1
            error_cycles += 1
            err_now = now_fn()
            err_record = CampaignCycleRecord(
                cycle=cycle,
                ts=err_now.isoformat(),
                goal=config.goal,
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
            except Exception:  # noqa: BLE001
                pass
            try:
                _log(agent, "campaign_cycle_error", {
                    "cycle": cycle,
                    "error": f"{type(exc).__name__}: {exc}",
                    "consecutive_errors": consecutive_errors,
                    "max_consecutive_errors": config.max_consecutive_errors,
                })
            except Exception:  # noqa: BLE001
                pass
            try:
                _emit_cycle(err_record)
            except Exception:  # noqa: BLE001
                pass
            if consecutive_errors >= config.max_consecutive_errors:
                stop_reason = (
                    f"error_stall:{consecutive_errors}_consecutive_cycle_errors"
                )
                status = "stopped"
                break
            continue

    totals = {
        "llm_calls": llm_calls_used,
        "cost_units": cost_units_used,
        "idle_cycles": idle_cycles,
        "useful_cycles": useful_cycles,
        "repeat_cycles": repeat_cycles,
        "error_cycles": error_cycles,
        "unproductive_cycles": unproductive_cycles,
        "proposals": proposals,
        "artifacts": artifacts,
        "wall_clock_seconds": round((now_fn() - started_at).total_seconds(), 1),
    }
    result = CampaignResult(
        status=status,
        goal=config.goal,
        stop_reason=stop_reason,
        cycles_run=len(records),
        records=records,
        totals=totals,
        clarification=clarification,
    )
    _log(agent, "campaign_stop", {
        "status": result.status,
        "goal": result.goal,
        "stop_reason": result.stop_reason,
        "cycles_run": result.cycles_run,
        "totals": totals,
    })
    return result
