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

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from core.best_next_action import BestNextAction


CampaignStatus = Literal["completed", "stopped"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CampaignConfig:
    """Immutable configuration for one autonomous campaign.

    A limit of ``0`` means "unlimited / off" for the two spend caps, matching
    the :class:`~core.budget_governor.BudgetLimits` convention. ``max_cycles``
    and ``max_idle_streak`` must be >= 1 (a campaign with zero cycles, or one
    that can never stop on idle, is a configuration error).
    """

    goal: str = "project health"
    max_cycles: int = 24
    max_llm_calls: int = 100      # 0 = unlimited
    max_cost_units: int = 0       # 0 = unlimited
    max_idle_streak: int = 3
    dry_run: bool = True
    report_every: int = 1
    idle_recheck_seconds: int = 600
    # Real wall-clock controls for multi-hour runs. A campaign with the two
    # defaults below behaves exactly as before: cycles run back-to-back with no
    # sleep and stop only on the cycle/idle/spend caps. Set them to pace a
    # genuine 24-48h run and to give the operator a hard real-time ceiling.
    max_wall_clock_seconds: int = 0   # 0 = unlimited (no real-time ceiling)
    cycle_pause_seconds: int = 0      # 0 = back-to-back (no inter-cycle sleep)

    def __post_init__(self) -> None:
        if self.max_cycles < 1:
            raise ValueError("max_cycles must be >= 1")
        if self.max_idle_streak < 1:
            raise ValueError("max_idle_streak must be >= 1")
        if self.max_llm_calls < 0:
            raise ValueError("max_llm_calls must be >= 0 (0 = unlimited)")
        if self.max_cost_units < 0:
            raise ValueError("max_cost_units must be >= 0 (0 = unlimited)")
        if self.report_every < 1:
            raise ValueError("report_every must be >= 1")
        if self.idle_recheck_seconds < 0:
            raise ValueError("idle_recheck_seconds must be >= 0")
        if self.max_wall_clock_seconds < 0:
            raise ValueError("max_wall_clock_seconds must be >= 0 (0 = unlimited)")
        if self.cycle_pause_seconds < 0:
            raise ValueError("cycle_pause_seconds must be >= 0 (0 = no pause)")


# ─────────────────────────────────────────────────────────────────────────────
# Per-cycle outcome of executing a useful action (returned by the collaborator)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CampaignActionOutcome:
    """What one useful (non-idle) cycle produced.

    ``result`` is the run status of the bounded pass (e.g. "completed" /
    "stopped" / "blocked"). ``proposal`` / ``artifact`` are short human-readable
    references when the cycle produced one; both stay ``None`` for a pass that
    only observed.
    """

    result: str
    llm_calls_spent: int = 0
    cost_units_spent: int = 0
    proposal: Optional[str] = None
    artifact: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Ledger record + ledger
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CampaignCycleRecord:
    """One immutable row of the campaign ledger.

    Every cycle — idle or working — produces exactly one record so the ledger
    is a complete, greppable account of where the keys went.
    """

    cycle: int
    ts: str
    goal: str
    action: str
    action_title: str
    severity: str
    priority: int
    risk: str
    idle: bool
    llm_calls_spent: int
    cost_units_spent: int
    result: str
    reason: str
    proposal: Optional[str] = None
    artifact: Optional[str] = None
    next_check_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "ts": self.ts,
            "goal": self.goal,
            "action": self.action,
            "action_title": self.action_title,
            "severity": self.severity,
            "priority": self.priority,
            "risk": self.risk,
            "idle": self.idle,
            "llm_calls_spent": self.llm_calls_spent,
            "cost_units_spent": self.cost_units_spent,
            "result": self.result,
            "reason": self.reason,
            "proposal": self.proposal,
            "artifact": self.artifact,
            "next_check_at": self.next_check_at,
        }

    def user_summary(self) -> str:
        if self.idle:
            return (
                f"[cycle {self.cycle}] IDLE (no LLM) "
                f"action={self.action} reason={self.reason}"
            )
        if self.result == "repeat":
            return (
                f"[cycle {self.cycle}] REPEAT (no LLM, skipped) "
                f"action={self.action} reason={self.reason}"
            )
        return (
            f"[cycle {self.cycle}] {self.result} "
            f"action={self.action} llm={self.llm_calls_spent} "
            f"cost={self.cost_units_spent}"
            + (f" proposal={self.proposal}" if self.proposal else "")
            + (f" artifact={self.artifact}" if self.artifact else "")
        )


class CampaignLedger:
    """Append-only ledger of campaign cycles.

    Plain JSONL (not the state-integrity envelope) on purpose: this is an audit
    trail an operator reads/greps directly to answer "what did it do with my
    keys?". The path lives under ``data/`` (gitignored runtime state).
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.records: list[CampaignCycleRecord] = []

    def append(self, record: CampaignCycleRecord) -> None:
        self.records.append(record)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Ledger reader — answers "what did it do with my keys?" from the audit trail
# ─────────────────────────────────────────────────────────────────────────────

def load_ledger_rows(path: Path) -> list[dict[str, Any]]:
    """Read the append-only campaign ledger JSONL into plain dict rows.

    Honest and tolerant: a missing file yields ``[]`` and a malformed line is
    skipped (the ledger is plain JSONL an operator may have hand-edited). The
    rows are the per-cycle ``CampaignCycleRecord.to_dict()`` shape, accumulated
    across every campaign that ever wrote to this file.
    """
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _format_ledger_row(row: dict[str, Any]) -> str:
    """Render one ledger row the way CampaignCycleRecord.user_summary would."""
    cycle = row.get("cycle", "?")
    action = row.get("action", "?")
    reason = row.get("reason", "")
    if row.get("idle"):
        return f"[cycle {cycle}] IDLE (no LLM) action={action} reason={reason}"
    if row.get("result") == "repeat":
        return f"[cycle {cycle}] REPEAT (no LLM, skipped) action={action} reason={reason}"
    proposal = row.get("proposal")
    artifact = row.get("artifact")
    return (
        f"[cycle {cycle}] {row.get('result', '?')} action={action} "
        f"llm={row.get('llm_calls_spent', 0)} cost={row.get('cost_units_spent', 0)}"
        + (f" proposal={proposal}" if proposal else "")
        + (f" artifact={artifact}" if artifact else "")
    )


def summarise_ledger(rows: list[dict[str, Any]], *, recent: int = 10) -> str:
    """Build an operator-readable digest of the campaign ledger.

    Pure (no I/O): takes the rows from :func:`load_ledger_rows` so it can be
    unit-tested without a file. Reports all-time totals, a per-result count,
    the distinct goals seen, and the most recent ``recent`` cycle rows.
    """
    if not rows:
        return (
            "=== campaign ledger ===\n"
            "(empty — no campaign cycles have been recorded yet)"
        )

    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    total = len(rows)
    llm_calls = sum(_as_int(r.get("llm_calls_spent")) for r in rows)
    cost_units = sum(_as_int(r.get("cost_units_spent")) for r in rows)
    idle = sum(1 for r in rows if r.get("idle"))
    repeats = sum(1 for r in rows if r.get("result") == "repeat")
    artifacts = sum(1 for r in rows if r.get("artifact"))
    proposals = sum(1 for r in rows if r.get("proposal"))
    useful = total - idle - repeats

    result_counts: dict[str, int] = {}
    for r in rows:
        key = "idle" if r.get("idle") else str(r.get("result", "?"))
        result_counts[key] = result_counts.get(key, 0) + 1
    by_result = ", ".join(f"{k}={v}" for k, v in sorted(result_counts.items()))

    goals: list[str] = []
    for r in rows:
        goal = str(r.get("goal", ""))
        if goal and goal not in goals:
            goals.append(goal)

    lines = [
        "=== campaign ledger ===",
        (
            f"cycles_logged={total}  useful={useful}  idle={idle}  "
            f"repeats={repeats}  llm_calls={llm_calls}  cost_units={cost_units}  "
            f"proposals={proposals}  artifacts={artifacts}"
        ),
        f"by_result: {by_result}",
        f"goals_seen ({len(goals)}): " + "; ".join(goals[:5])
        + (" …" if len(goals) > 5 else ""),
    ]
    tail = rows[-recent:] if recent > 0 else rows
    lines.append(f"recent {len(tail)} cycle(s):")
    for r in tail:
        lines.append(f"  {_format_ledger_row(r)}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Final result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CampaignResult:
    status: CampaignStatus
    goal: str
    stop_reason: str
    cycles_run: int
    records: list[CampaignCycleRecord] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "goal": self.goal,
            "stop_reason": self.stop_reason,
            "cycles_run": self.cycles_run,
            "totals": self.totals,
            "records": [r.to_dict() for r in self.records],
        }

    def user_summary(self) -> str:
        lines = [
            "=== autonomous campaign ===",
            f"status={self.status}  goal={self.goal!r}  "
            f"stop_reason={self.stop_reason or '-'}",
            (
                f"cycles={self.cycles_run}  "
                f"useful={self.totals.get('useful_cycles', 0)}  "
                f"idle={self.totals.get('idle_cycles', 0)}  "
                f"repeats={self.totals.get('repeat_cycles', 0)}  "
                f"llm_calls={self.totals.get('llm_calls', 0)}  "
                f"cost_units={self.totals.get('cost_units', 0)}  "
                f"proposals={self.totals.get('proposals', 0)}  "
                f"artifacts={self.totals.get('artifacts', 0)}"
            ),
        ]
        for record in self.records:
            lines.append(f"  {record.user_summary()}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# The pure loop
# ─────────────────────────────────────────────────────────────────────────────

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
    stop_reason = ""
    status: CampaignStatus = "completed"
    started_at = now_fn()

    _log(agent, "campaign_start", {
        "goal": config.goal,
        "max_cycles": config.max_cycles,
        "max_llm_calls": config.max_llm_calls,
        "max_cost_units": config.max_cost_units,
        "max_idle_streak": config.max_idle_streak,
        "dry_run": config.dry_run,
    })

    for cycle in range(1, config.max_cycles + 1):
        # ── pace: a real wall-clock gap between cycles (never before the
        #    first). Capped to whatever real time is left so the pause itself
        #    can never sleep meaningfully past the hard ceiling. ────────────
        if cycle > 1 and config.cycle_pause_seconds:
            pause = float(config.cycle_pause_seconds)
            if config.max_wall_clock_seconds:
                remaining = config.max_wall_clock_seconds - (
                    now_fn() - started_at
                ).total_seconds()
                pause = min(pause, max(0.0, remaining))
            if pause > 0:
                sleep_fn(pause)

        # ── wall-clock budget: stop BEFORE starting new work past the
        #    deadline (a hard real-time ceiling for a 24-48h run). ──────────
        if config.max_wall_clock_seconds:
            elapsed = (now_fn() - started_at).total_seconds()
            if elapsed >= config.max_wall_clock_seconds:
                stop_reason = (
                    f"wall_clock_exhausted:{int(elapsed)}s/"
                    f"{config.max_wall_clock_seconds}s"
                )
                status = "stopped"
                break

        # ── budget pre-check: stop BEFORE the next spend, never after ────────
        if config.max_llm_calls and llm_calls_used >= config.max_llm_calls:
            stop_reason = f"budget_exhausted:llm_calls={llm_calls_used}/{config.max_llm_calls}"
            status = "stopped"
            break
        if config.max_cost_units and cost_units_used >= config.max_cost_units:
            stop_reason = f"budget_exhausted:cost_units={cost_units_used}/{config.max_cost_units}"
            status = "stopped"
            break

        signals = gather(agent, workspace, approval_inbox)
        action: BestNextAction = signals["action"]
        now = now_fn()

        # ── IDLE cycle: no high-priority action -> do NOT call the LLM ───────
        if action.priority <= 0:
            idle_streak += 1
            idle_cycles += 1
            next_check = (
                (now + timedelta(seconds=config.idle_recheck_seconds)).isoformat()
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

            if idle_streak >= config.max_idle_streak:
                stop_reason = f"idle_stall:{idle_streak}_consecutive_idle_cycles"
                status = "stopped"
                break
            continue

        # ── USEFUL cycle: a real action -> one bounded, gated pass ───────────
        # B — signal dedup: an action already attempted this campaign whose
        # earlier pass did not clear the signal is a REPEAT. Re-running it
        # identically would spend again to reach the same dead end (the smoke
        # showed two identical `restore_daemon_liveness` cycles), so skip
        # execution, record it honestly, and let the no-progress streak stop
        # the campaign — the same "empty cycles -> ask the operator" guarantee
        # as idle, just for "nothing NEW to do" instead of "nothing to do".
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

            if idle_streak >= config.max_idle_streak:
                stop_reason = f"no_progress_stall:{idle_streak}_cycles_without_new_action"
                status = "stopped"
                break
            continue

        # ── genuinely NEW action: reset the no-progress streak and execute ───
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

    totals = {
        "llm_calls": llm_calls_used,
        "cost_units": cost_units_used,
        "idle_cycles": idle_cycles,
        "useful_cycles": useful_cycles,
        "repeat_cycles": repeat_cycles,
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
    )
    _log(agent, "campaign_stop", {
        "status": result.status,
        "goal": result.goal,
        "stop_reason": result.stop_reason,
        "cycles_run": result.cycles_run,
        "totals": totals,
    })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Default collaborators (lazily import the heavy runtime pieces)
# ─────────────────────────────────────────────────────────────────────────────

def _default_gather_signals(agent: Any, workspace: Any, approval_inbox: Any) -> dict[str, Any]:
    """Collect every signal READ-ONLY, then pick the single best next action.

    Mirrors ``scripts/first_live_probe._gather_signals`` but reuses a passed
    approval inbox when supplied. No mutation, no execution, no network.
    """
    import agent_tick
    from core.alert_ack import AlertAckStore
    from core.approval_inbox import ApprovalInbox
    from core.approval_triage import triage_inbox
    from core.best_next_action import select_best_next_action

    ws = Path(workspace)
    heartbeat = agent_tick._read_heartbeat(ws)
    age = agent_tick._heartbeat_age_seconds(heartbeat)
    hb = heartbeat or {}

    inbox = approval_inbox or ApprovalInbox(path=ws / "data" / "approval_inbox.jsonl")
    triage = triage_inbox(inbox.pending())

    ack_store = AlertAckStore(path=ws / "data" / "alert_acknowledgements.jsonl")
    acknowledged = ack_store.active_actions()

    action = select_best_next_action(
        result_status=str(hb.get("result_status", "none")),
        tests_health=str(hb.get("tests_health", "none")),
        dry_run_streak=int(hb.get("dry_run_streak", 0) or 0),
        heartbeat_missing=heartbeat is None,
        heartbeat_stale=agent_tick._is_stale(age),
        heartbeat_age_seconds=age,
        last_event=str(hb.get("event", "")),
        tick_error=hb.get("error"),
        triage=triage,
        inbox_pending=triage.total_pending,
        acknowledged=acknowledged,
    )
    return {"heartbeat": hb, "age": age, "triage": triage, "action": action}


def _cost_totals(agent: Any) -> tuple[int, int]:
    """Read (llm_calls, cost_units) totals from the agent's persistent ledger.

    Defensive at the boundary: an arbitrary agent may not expose the full
    router->usage_ledger->budget_ledger chain. Returns (0, 0) when it is
    absent rather than guessing a shape.
    """
    try:
        usage_ledger = getattr(agent.model_router, "usage_ledger", None)
        ledger = getattr(usage_ledger, "budget_ledger", None)
        if ledger is None:
            return (0, 0)
        totals = ledger.snapshot().get("totals", {})
        return (int(totals.get("llm_calls", 0)), int(totals.get("model_cost_units", 0)))
    except (AttributeError, TypeError, ValueError):
        return (0, 0)


def _action_focused_goal(goal: str, action: BestNextAction) -> str:
    """Turn the picked action into a focused, read-only reasoning question.

    GAP A — the campaign cycle is action-COUPLED: instead of running a generic
    health pass decoupled from the picked signal, the agent reasons about the
    SINGLE highest-priority action toward the campaign goal. Effects are blocked
    in dry-run, so this asks the model "what is the one useful next step here?"
    without performing it. Pure string builder so it can be unit-tested without
    a live agent.
    """
    reason = (action.reason or "").strip()
    evidence = "; ".join(e for e in action.evidence[:3] if e)
    parts = [
        f"Campaign goal: {goal.strip()}.",
        f"The single highest-priority signal right now is '{action.action}' — {action.title}.",
    ]
    if reason:
        parts.append(f"Why it matters: {reason}")
    if evidence:
        parts.append(f"Evidence: {evidence}.")
    parts.append(
        "Decide the ONE most useful next step a human should take to move the "
        "goal forward, and justify it with the evidence. Reason read-only — do "
        "not perform any effects."
    )
    return " ".join(parts)


def _default_execute_action(
    *,
    agent: Any,
    workspace: Any,
    action: BestNextAction,
    config: CampaignConfig,
    approval_inbox: Any = None,
) -> CampaignActionOutcome:
    """Run ONE bounded, action-coupled pass through the existing AutonomousRuntime.

    GAP A fix: the pass is driven by the PICKED action (not a generic health
    sweep). The action becomes a focused read-only question answered by the
    ``goal`` task — real LLM reasoning about the single highest-priority signal.
    Opens no new effect path: effects flow only through the runtime's PolicyGate
    + approval inbox, and ``dry_run`` (which blocks file_write/shell_exec inside
    the goal task) follows the campaign config. Cost is measured as the delta of
    the agent's persistent budget ledger around the pass, so the ledger records
    the REAL spend, not a guess.
    """
    from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig
    from core.budget_governor import BudgetLimits

    llm_before, cost_before = _cost_totals(agent)

    focused_goal = _action_focused_goal(config.goal, action)
    runtime = AutonomousRuntime(agent, workspace=workspace, approval_inbox=approval_inbox)
    report = runtime.run(
        AutonomousRuntimeConfig(
            goal=focused_goal,
            dry_run=config.dry_run,
            # status, learn, goal — the goal task does the action-coupled
            # reasoning; the two read-only preambles cost nothing.
            limit=3,
            include_tests=False,
            include_goal=True,
            budgets=BudgetLimits(max_agent_runs=1),
            enable_reflection=False,
        )
    )

    llm_after, cost_after = _cost_totals(agent)

    pending = 0
    try:
        pending = int(report.approvals.get("pending", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        pending = 0
    proposal = f"approvals_pending={pending}" if pending else None

    # The goal task's answer is the cycle's measurable output — surface a short
    # digest in the ledger so an operator can read what the agent concluded.
    artifact = None
    for task_report in getattr(report, "tasks", []) or []:
        if getattr(task_report.task, "kind", "") == "goal":
            answer = (task_report.details or {}).get("answer")
            if answer:
                digest = " ".join(str(answer).split())[:160]
                if digest:
                    artifact = f"reasoning: {digest}"
            break

    return CampaignActionOutcome(
        result=report.status,
        llm_calls_spent=max(0, llm_after - llm_before),
        cost_units_spent=max(0, cost_after - cost_before),
        proposal=proposal,
        artifact=artifact,
    )


def _log(agent: Any, event: str, payload: dict[str, Any]) -> None:
    log = getattr(agent, "log", None)
    if log is None:
        return
    try:
        log.log(event, payload)
    except (AttributeError, TypeError):
        pass
