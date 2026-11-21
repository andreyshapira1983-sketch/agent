"""Daemon tick for Task Scheduler or cron: one bounded pass over schedules and tasks, then exit."""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import sys
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core import heartbeat_io as _hb
from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH
from core.llm import ensure_roster_home
from core.rule_approved_apply import drain_and_log
from core.task_queue import DEFAULT_RUNTIME_TASKS_PATH

if TYPE_CHECKING:
    from core.approval_inbox import ApprovalInbox


# ── paths ─────────────────────────────────────────────────────────────────────

WORKSPACE_DEFAULT = Path(os.environ.get("AGENT_WORKSPACE", Path(__file__).parent)).resolve()

DATA_DIR           = "data"
APPROVAL_INBOX_PATH = DEFAULT_APPROVAL_INBOX_PATH
SCHEDULES_PATH     = "data/runtime_schedules.jsonl"
# The queue the operator writes to; imported, not re-declared (tests/test_one_task_store.py).
TASK_QUEUE_PATH    = str(DEFAULT_RUNTIME_TASKS_PATH)
INCIDENT_LOG_PATH  = "data/incidents.jsonl"
HEARTBEAT_PATH     = _hb.HEARTBEAT_PATH
# Every queue consumer holds this lock while draining; same path as app/single_instance.py.
DAEMON_LOCK_PATH   = "data/daemon.lock"

# Goal-pick attempts at campaign start: one rejected pick must not end the run.
_GOAL_PICK_ATTEMPTS = 3
BUDGET_LEDGER_PATH = "data/budget_ledger.jsonl"
BUDGET_CONFIG_PATH = "config/budget_limits.json"
# Self-build producer cooldown state: ISO time of the last successfully proposed item.
SELF_BUILD_STATE_PATH = "data/self_build_producer_state.json"
# Hours between self-build proposals; override with AGENT_SELF_BUILD_COOLDOWN_HOURS.
SELF_BUILD_COOLDOWN_HOURS_DEFAULT = 12.0

# Re-exported from core/heartbeat_io.py (the core may not import this script).
EXPECTED_TICK_INTERVAL_SECONDS = _hb.EXPECTED_TICK_INTERVAL_SECONDS
STALENESS_FACTOR = _hb.STALENESS_FACTOR

# Unattended agent memory: reads experience but never replays a stored answer; durable
# writes only to episode and hygiene (default-deny for the rest); no cross-run session memory.
UNATTENDED_MEMORY_PROFILE = {
    "with_memory": False,
    "with_experience": True,
    "episodic_replay": False,
    "durable_writes": frozenset({"episode", "hygiene"}),
}


def unattended_memory_profile(workspace, env=None) -> dict:
    """Unattended memory profile: the constant above, plus two sinks under an active sandbox grant.

    Call it with a variable named `workspace`: a test compares the four build sites literally.
    """
    from core.burn_in_sandbox import memory_profile_for

    return memory_profile_for(UNATTENDED_MEMORY_PROFILE, workspace, env=env)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_tick(workspace: Path, payload: dict) -> None:
    log_path = _hb.tick_log_path(workspace)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _now_iso(), **payload}, ensure_ascii=False) + "\n")


_write_heartbeat = _hb.write_heartbeat
_read_heartbeat = _hb.read_heartbeat
_heartbeat_age_seconds = _hb.heartbeat_age_seconds
_is_stale = _hb.is_stale


def _check_budget_kill_switch(workspace: Path):
    """Evaluate and latch the budget kill-switch from the ledger alone (no agent, no model).

    Returns a KillSwitchState; `.active` means the day budget is spent and model work is skipped.
    """
    from core.budget_kill_switch import BudgetKillSwitch, default_path
    from core.budget_ledger import BudgetLedger

    ledger = BudgetLedger.from_env(
        path=workspace / BUDGET_LEDGER_PATH,
        config_path=workspace / BUDGET_CONFIG_PATH,
    )
    kill_switch = BudgetKillSwitch(path=default_path(workspace))
    return kill_switch.engage_if_needed(ledger.snapshot())


def _dry_run_visibility(
    *,
    dry_run: bool,
    previous_streak: int = 0,
    processed_effects: int = 0,
    did_work: bool = True,
) -> dict:
    """Pure observability fields for this tick: mode, effects, processed_effects, dry_run_streak.

    The streak counts dry-run ticks that did work; an idle tick carries it unchanged and a live
    tick resets it to 0. `effects` is what policy allows, not what was applied.
    """
    try:
        prev = max(0, int(previous_streak))
    except (TypeError, ValueError):
        prev = 0
    try:
        applied = max(0, int(processed_effects))
    except (TypeError, ValueError):
        applied = 0
    if not dry_run:
        streak = 0
    elif did_work:
        streak = prev + 1
    else:
        streak = prev  # idle no-op tick — carry forward, do not inflate
    return {
        "mode": "dry_run" if dry_run else "live",
        "effects": "disabled" if dry_run else "enabled",
        "processed_effects": applied,
        "dry_run_streak": streak,
    }


def _log_idle_tick(workspace: Path) -> None:
    """Log an empty queue, then what the agent would do anyway (a sensor, not a gate)."""
    _log_tick(workspace, {"event": "no_pending_tasks"})
    try:
        from core.self_build_memory import idle_self_direction
        _log_tick(workspace, {"event": "self_direction",
                              **idle_self_direction(workspace)})
    except Exception as exc:  # noqa: BLE001
        _log_tick(workspace, {"event": "self_direction_error",
                              "error": f"{type(exc).__name__}: {exc}"})


def _classify_test_health(tests_result: dict | None) -> str:
    """Map a tests_result payload to "none", "pass", "fail" or "inconclusive".

    A run that timed out, has no exit code or ran zero tests is inconclusive, never a pass.
    """
    if not tests_result:
        return "none"
    if tests_result.get("timed_out") or tests_result.get("exit_code") is None:
        return "inconclusive"
    failed = int(tests_result.get("failed", 0) or 0) + int(
        tests_result.get("errors", 0) or 0
    )
    if failed > 0:
        return "fail"
    if int(tests_result.get("passed", 0) or 0) == 0:
        return "inconclusive"
    return "pass"



# ── status-only mode ──────────────────────────────────────────────────────────

# One action repeated on one goal this many times is shown as a loop (attention, not a ban).
_TRAJECTORY_REPEAT_MIN = 3

# Window after which a loop counts as over.
_TRAJECTORY_WINDOW_HOURS = 48


def _trajectory_status_lines(workspace: Path) -> list[str]:
    """Status lines about a repeating trajectory, or none; read-only, never raises."""
    lines: list[str] = []
    try:
        from datetime import datetime, timedelta, timezone

        from core.state_integrity import read_state_jsonl

        path = workspace / DATA_DIR / "campaign_ledger.jsonl"
        if not path.exists():
            return lines
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=_TRAJECTORY_WINDOW_HOURS
        )
        groups: dict[tuple[str, str], list[dict]] = {}
        for row in read_state_jsonl(path):
            try:
                when = datetime.fromisoformat(str(row.get("ts")))
            except (TypeError, ValueError):
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when < cutoff:
                continue
            key = (str(row.get("goal") or ""), str(row.get("action") or ""))
            groups.setdefault(key, []).append(row)
    except (OSError, ValueError):  # pragma: no cover — reading must not break the status line
        return []

    for (goal, action), rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(rows) < _TRAJECTORY_REPEAT_MIN or not action:
            continue
        cost = sum(int(r.get("cost_units_spent") or 0) for r in rows)
        calls = sum(int(r.get("llm_calls_spent") or 0) for r in rows)
        lines.append(
            f"Trajectory: {len(rows)} cycles of ONE action "
            f"'{action}' in {_TRAJECTORY_WINDOW_HOURS}h — {calls} model call(s), "
            f"{cost} cost unit(s) — goal: {goal[:60]!r}"
        )
    return lines[:3]


def _quarantine_status_lines(workspace: Path) -> list[str]:
    """Status lines about quarantined state rows, or none; read-only, never raises.

    A row dropped from a cap store reads as spending room, so the loss must be visible.
    """
    from core.state_integrity import quarantine_dir_for

    lines: list[str] = []
    try:
        qdir = quarantine_dir_for(workspace / DATA_DIR / "x.jsonl")
        if not qdir.exists():
            return lines
        entries = sorted(f for f in qdir.iterdir() if f.is_file())
        if not entries:
            return lines
        lines.append(
            f"State integrity: {len(entries)} quarantined row file(s) — "
            f"rows dropped from state stores, and a dropped row in a CAP "
            f"store reads as spending room:"
        )
        for f in entries[:10]:
            lines.append(f"  {f.name}")
        if len(entries) > 10:
            lines.append(f"  ... and {len(entries) - 10} more")
    except OSError:
        return []
    return lines


# Heartbeat events meaning the tick failed; by substring, so a new *_error never reads as alive.
def _is_failure_event(event: object) -> bool:
    text = str(event or "").lower()
    return "error" in text or "exception" in text or "fail" in text


def _print_status(workspace: Path) -> int:
    """Print pending inbox items and exit. No agent is created."""
    from core.approval_inbox import ApprovalInbox

    # Daemon liveness first: distinguish "idle (nothing due)" from "not running".
    heartbeat = _read_heartbeat(workspace)
    age = _heartbeat_age_seconds(heartbeat)
    if heartbeat is None:
        print("Daemon: no heartbeat recorded yet (never ticked).", file=sys.stderr)
    else:
        age_min = (age or 0) / 60.0
        last_event = heartbeat.get("event", "?")
        if _is_stale(age):
            print(
                f"Daemon: STALE — last tick {age_min:.1f} min ago "
                f"(event={last_event}); expected every "
                f"{EXPECTED_TICK_INTERVAL_SECONDS // 60} min. "
                "Daemon may not be running.",
                file=sys.stderr,
            )
        elif _is_failure_event(last_event):
            # The heartbeat is written on failure too: a fresh failed tick must not say "alive".
            print(
                f"Daemon: FAILING — last tick {age_min:.1f} min ago ended in "
                f"{last_event}. The clock is ticking and no work is landing.",
                file=sys.stderr,
            )
        else:
            print(
                f"Daemon: alive — last tick {age_min:.1f} min ago "
                f"(event={last_event}).",
                file=sys.stderr,
            )

    from core.incident import IncidentLog

    incident_line = IncidentLog(path=workspace / INCIDENT_LOG_PATH).status_line()
    if incident_line:
        print(incident_line, file=sys.stderr)

    if heartbeat is not None:
        mode = heartbeat.get("mode", "?")
        effects = heartbeat.get("effects", "?")
        streak = heartbeat.get("dry_run_streak", "?")
        # processed_effects is not shown: it is always written as 0 and would read as a count.
        print(
            f"Mode: {mode} (effects={effects}, dry_run_streak={streak})",
            file=sys.stderr,
        )

    inbox = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
    pending = inbox.pending()
    if not pending:
        print("Inbox: no pending items.", file=sys.stderr)
    else:
        print(f"Inbox: {len(pending)} pending item(s):", file=sys.stderr)
        for item in pending:
            print(f"  [{item.id}] {item.operation}: {item.summary}", file=sys.stderr)

    # Self-build producer state; read-only, never raises.
    for line in _self_build_status_block(workspace, heartbeat, pending, inbox):
        print(line, file=sys.stderr)
    for line in _quarantine_status_lines(workspace):
        print(line, file=sys.stderr)
    for line in _trajectory_status_lines(workspace):
        print(line, file=sys.stderr)
    from core.charter_goal import charter_status_lines

    for line in charter_status_lines(workspace):
        print(line, file=sys.stderr)
    from core.subagent_quarantine import quarantine_status_lines

    for line in quarantine_status_lines(workspace):
        print(line, file=sys.stderr)
    return 0


# ── repair proposal after failed tests ───────────────────────────────────────

def _repair_target_from_failures(failed_names: list[str], workspace: Path) -> str | None:
    """The one existing test file the failures point at, else None (never guess between files)."""
    files: list[str] = []
    for name in failed_names:
        path = (name or "").split("::", 1)[0].strip()
        if not path:
            continue
        if (workspace / path).is_file() and path not in files:
            files.append(path)
    return files[0] if len(files) == 1 else None


# Exception names are the stable part of a diagnosis; the rest is reworded every tick.
_CAUSE_RE = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Error|Exception|Warning|Timeout))\b")


def _cause_fingerprint(text: str) -> str:
    """A failure diagnosis reduced to its sorted exception names ("" when it names none).

    Not the free text: every rewording would open a new repair proposal.
    """
    found = sorted({m.group(1) for m in _CAUSE_RE.finditer(text or "")})
    return "|".join(found)


def _repair_dedup_key(
    target_path: str, failed_names: list[str], cause: str = ""
) -> str:
    """Stable name of one breakage: repair target + failing tests (order-free) + cause."""
    fingerprint = "|".join(sorted({str(n) for n in failed_names}))
    if cause:
        fingerprint += "##" + cause
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    return f"repair:{target_path}:{digest}"


def _maybe_propose_repair(
    workspace: Path,
    test_report: dict,
    inbox: ApprovalInbox,
    agent: object,
) -> dict:
    """If tests failed, ask RepairProposalGenerator for a plan and inbox it."""
    failed = int(test_report.get("failed", 0) or 0) + int(test_report.get("errors", 0) or 0)
    if failed == 0:
        return {"repair_proposed": False, "reason": "all tests passed"}

    failed_names: list[str] = test_report.get("failed_tests", []) or []

    target_path = _repair_target_from_failures(failed_names, workspace)
    if target_path is None:
        return {
            "repair_proposed": False,
            "reason": (
                "could not determine a single repair target from failing tests: "
                f"{failed_names[:5]}"
            ),
        }

    try:
        from core.repair_proposal import RepairProposalGenerator

        llm = getattr(agent, "llm", None)
        if llm is None:
            return {"repair_proposed": False, "reason": "no llm on agent"}

        gen = RepairProposalGenerator(llm=llm, workspace_root=workspace)
        report = gen.generate(target_path=target_path)

        if report.status == "proposed" and report.proposal is not None:
            prop = report.proposal
            # RepairProposal fields are path/reason/proposed_content, not target_file/description.
            description = (prop.reason or report.diagnosis or "").strip() or "(no description)"
            summary_lines = [
                f"{failed} test(s) failed: {', '.join(failed_names[:5])}",
                f"Proposed fix → {prop.path}: {description}",
                f"Confidence: {report.confidence:.0%}",
            ]
            inbox.add(
                operation="repair_proposal",
                summary="\n".join(summary_lines),
                risk="reversible",
                reasons=(
                    f"{failed} failing test(s)",
                    f"evidence: {', '.join(report.evidence[:3])}",
                ),
                # One breakage, one request: the inbox dedupes only by key, and pending duplicates
                # also block the self-apply lane. The key names the breakage, not the fix.
                dedup_key=_repair_dedup_key(
                    target_path, failed_names,
                    _cause_fingerprint(
                        f"{report.diagnosis or ''} {' '.join(report.evidence or ())}"
                    ),
                ),
                payload={
                    "failed_count": failed,
                    "failed_tests": failed_names,
                    "target_file": prop.path,
                    "proposed_content_preview": (
                        prop.proposed_content[:500] if prop.proposed_content else ""
                    ),
                    "confidence": report.confidence,
                    "diagnosis": report.diagnosis,
                },
            )
            return {"repair_proposed": True, "target": prop.path}
    except Exception as exc:  # noqa: BLE001
        return {"repair_proposed": False, "reason": f"exception: {exc}"}
    else:
        return {"repair_proposed": False, "reason": f"generator status: {report.status}"}


# ── autonomous self-build producer wiring ─────────────────────────────────────


def _self_build_cooldown_hours() -> float:
    """Cooldown hours from AGENT_SELF_BUILD_COOLDOWN_HOURS, read at call time, else the default."""
    raw = os.environ.get("AGENT_SELF_BUILD_COOLDOWN_HOURS")
    if raw is None or not str(raw).strip():
        return SELF_BUILD_COOLDOWN_HOURS_DEFAULT
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        return SELF_BUILD_COOLDOWN_HOURS_DEFAULT
    return hours if hours >= 0 else SELF_BUILD_COOLDOWN_HOURS_DEFAULT


def _read_producer_state(workspace: Path) -> dict:
    path = workspace / SELF_BUILD_STATE_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_producer_state(workspace: Path, last_proposed_at: str) -> None:
    path = workspace / SELF_BUILD_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"last_proposed_at": last_proposed_at}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # atomic swap


def _cooldown_remaining_seconds(
    state: dict, *, cooldown_hours: float, now: datetime | None = None
) -> float:
    """Seconds still to wait before another proposal is allowed (0 = ready)."""
    if cooldown_hours <= 0:
        return 0.0
    last = state.get("last_proposed_at")
    if not last:
        return 0.0
    try:
        when = datetime.fromisoformat(str(last))
    except (ValueError, TypeError):
        return 0.0  # unparseable timestamp never blocks a fresh proposal
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    elapsed = (now - when).total_seconds()
    remaining = cooldown_hours * 3600.0 - elapsed
    return remaining if remaining > 0 else 0.0


def _format_remaining(seconds: float) -> str:
    """Human-readable cooldown remaining, e.g. ``2h 5m`` / ``7m`` / ``<1m``."""
    total = int(seconds) if seconds and seconds > 0 else 0
    hours, rem = divmod(total, 3600)
    minutes, _ = divmod(rem, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    if minutes:
        return f"{minutes}m"
    return "<1m"


def _self_build_status_lines(
    heartbeat: dict | None,
    state: dict | None,
    *,
    cooldown_hours: float,
    pending_self_apply: int,
    approved_self_apply: int,
    now: datetime | None = None,
) -> list[str]:
    """Render the read-only Self-build block for --status; pure, never raises.

    The last status comes from the heartbeat (historical); only the cooldown is computed live.
    """
    hb = heartbeat if isinstance(heartbeat, dict) else {}
    st = state if isinstance(state, dict) else {}
    lines: list[str] = []

    last_status = hb.get("self_build_status") or "none"
    if last_status == "none":
        lines.append("Self-build: last self-build status: never ran")
    else:
        lines.append(
            f"Self-build: last self-build status: {last_status} "
            "(historical, from last tick — not a live gate)"
        )

    last_proposed = st.get("last_proposed_at")
    if not st:
        health = "missing"
    elif not last_proposed:
        health = "no-timestamp"
    else:
        try:
            datetime.fromisoformat(str(last_proposed))
            health = "ok"
        except (ValueError, TypeError):
            health = "bad-timestamp"
    lines.append(f"  last_proposed_at: {last_proposed or 'none'} (state file: {health})")

    remaining = _cooldown_remaining_seconds(st, cooldown_hours=cooldown_hours, now=now)
    if remaining <= 0:
        lines.append("  cooldown: ready (live)")
    else:
        lines.append(f"  cooldown: {_format_remaining(remaining)} remaining (live)")

    lines.append(f"  last approval_id: {hb.get('self_build_approval_id') or 'none'}")
    lines.append(f"  next_human_action: {hb.get('self_build_next_human_action') or 'none'}")
    lines.append(
        f"  self_apply_lane.run: {pending_self_apply} pending, "
        f"{approved_self_apply} approved ready for :self-apply-run"
    )
    return lines


def _provider_health_line(workspace: Path) -> str:
    """One line naming any provider the router is currently skipping, so demotions are visible."""
    try:
        from core.model_usage import ModelUsageLedger, ModelUsageLimits

        ledger = ModelUsageLedger(
            path=workspace / DATA_DIR / "model_usage.jsonl",
            limits=ModelUsageLimits(),
        )
        seen: list[str] = []
        for row in ledger._recent_rows_for_any(limit=200):
            provider = str(row.get("provider") or "").strip().lower()
            if provider and provider not in seen:
                seen.append(provider)
        if not seen:
            return "no calls recorded"
        parts = []
        for provider in seen:
            reason = ledger.provider_unhealthy(provider)
            if reason:
                parts.append(f"{provider} SKIPPED ({reason})")
                continue
            # "ok" means the last recorded call succeeded, not merely "not in cooldown now".
            rows = ledger._recent_rows_for(provider, limit=1)
            last = str((rows[-1] if rows else {}).get("status") or "?")
            if last == "success":
                parts.append(f"{provider} ok")
            else:
                err = str((rows[-1] if rows else {}).get("error") or "")[:60]
                parts.append(f"{provider} last={last} ({err})" if err
                             else f"{provider} last={last}")
        return "; ".join(parts)
    except Exception as exc:  # noqa: BLE001 — operator status must never crash
        return f"unavailable ({type(exc).__name__})"


def _self_build_status_block(
    workspace: Path, heartbeat: dict | None, pending_items: list, inbox: ApprovalInbox
) -> list[str]:
    """Producer state and inbox counts as status lines; never raises.

    Reuses `pending_items`: a second inbox.pending() would enforce the TTL again on read.
    """
    try:
        from core.self_apply_bridge import SELF_APPLY_OPERATION

        state = _read_producer_state(workspace)
        cooldown_hours = _self_build_cooldown_hours()
        pending_sa = sum(
            1 for i in pending_items if getattr(i, "operation", "") == SELF_APPLY_OPERATION
        )
        approved_sa = sum(
            1
            for i in inbox.list(status="approved")
            if getattr(i, "operation", "") == SELF_APPLY_OPERATION
        )
        lines = _self_build_status_lines(
            heartbeat,
            state,
            cooldown_hours=cooldown_hours,
            pending_self_apply=pending_sa,
            approved_self_apply=approved_sa,
        )
        # Subagent ledger summary; read-only, runs nothing.
        try:
            from core.subagent_registry import SubagentRegistry

            lines.append(f"  subagents: {SubagentRegistry.load(workspace).summary_line()}")
        except Exception:  # noqa: BLE001
            lines.append("  subagents: unavailable")
        lines.append(f"  providers: {_provider_health_line(workspace)}")
    except Exception as exc:  # noqa: BLE001 — operator status must never crash
        return [f"Self-build: status unavailable ({type(exc).__name__})"]
    else:
        return lines


def _maybe_produce_self_build(
    workspace: Path,
    inbox: ApprovalInbox,
    *,
    dry_run: bool = True,
    now_iso: str | None = None,
    cooldown_hours: float | None = None,
    build_agent_fn: Callable[[Path], Any] | None = None,
    producer_fn: Callable[..., Any] | None = None,
    vcs: Any = None,
    kill_switch: Any = None,
    budget_snapshot: Any = None,
) -> dict:
    """Run the self-build producer at most once per tick, behind a persistent cooldown gate.

    The cooldown is checked before any agent is built; the producer owns every other gate.
    At most one inbox item, nothing applied; any exception becomes status="error".
    """
    now_iso = now_iso or _now_iso()
    try:
        now_dt = datetime.fromisoformat(now_iso)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        now_dt = datetime.now(timezone.utc)

    hours = cooldown_hours if cooldown_hours is not None else _self_build_cooldown_hours()

    # Cooldown gate (the only gate this layer owns), before any agent is built.
    remaining = _cooldown_remaining_seconds(
        _read_producer_state(workspace), cooldown_hours=hours, now=now_dt
    )
    if remaining > 0:
        return {
            "self_build_status": "cooldown_wait",
            "cooldown_remaining_s": int(remaining),
            "next_human_action": "",
        }

    try:
        # Lazy imports: keep module import cheap and match the tick's dotenv order.
        from core.budget_kill_switch import BudgetKillSwitch, default_path
        from core.budget_ledger import BudgetLedger
        from core.safe_vcs import SafeVCS
        from core.self_build_memory import record_self_build_episode
        from core.self_build_producer import produce_self_apply_proposal

        produce = producer_fn or produce_self_apply_proposal

        if budget_snapshot is None:
            ledger = BudgetLedger.from_env(
                path=workspace / BUDGET_LEDGER_PATH,
                config_path=workspace / BUDGET_CONFIG_PATH,
            )
            budget_snapshot = ledger.snapshot()
        if kill_switch is None:
            kill_switch = BudgetKillSwitch(path=default_path(workspace)).status(
                budget_snapshot
            )
        if vcs is None:
            vcs = SafeVCS(workspace=workspace)

        # A plain import, never a module name in a string: a moved build_agent must break loudly.
        if build_agent_fn is not None:
            builder = build_agent_fn
        else:
            # Lazy: app.bootstrap loads the whole agent graph; an injected builder skips that.
            from app.bootstrap import build_agent as _build_agent

            def builder(workspace):
                return _build_agent(
                    workspace, approval_provider=None,
                    **unattended_memory_profile(workspace),
                )

        agent = builder(workspace)
        llm = agent.model_router.for_role("synthesizer")

        # Subagent ledger for role performance; best effort, its failure never breaks the tick.
        registry = None
        try:
            from core.subagent_registry import SubagentRegistry
            registry = SubagentRegistry.load(workspace)
        except Exception:  # noqa: BLE001
            registry = None

        report = produce(
            workspace=workspace,
            inbox=inbox,
            llm=llm,
            vcs=vcs,
            budget_snapshot=budget_snapshot,
            kill_switch=kill_switch,
            now_iso=now_iso,
            registry=registry,
        )
        result = report.to_dict() if hasattr(report, "to_dict") else dict(report)
        status = result.get("status", "error")

        # Only a created proposal spends the cooldown; any other outcome may retry next tick.
        if status == "proposed":
            _write_producer_state(workspace, now_iso)

        # Journal the outcome so a veto teaches the next tick (best effort, never raises).
        record_self_build_episode(agent, kind="self-build-produce", result=result)

        return {
            "self_build_status": status,
            "approval_id": result.get("approval_id"),
            "target_path": result.get("target_path"),
            "next_human_action": result.get("next_human_action", ""),
            # Explains every non-exception outcome (dirty_tree_wait, veto, no_patch, ...).
            "reason": result.get("reason", ""),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "self_build_status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "next_human_action": "",
        }


def _self_build_status_line(sb_status: str, sb: dict) -> str:
    """Operator line for a self-build outcome: status, then error or reason, then next action."""
    detail = sb.get("error") or sb.get("reason") or ""
    advice = sb.get("next_human_action") or ""
    line = f"[agent_tick] Self-build producer: {sb_status}"
    if detail:
        line += f" - {detail}"
    line += "."
    if advice:
        line += f" {advice}"
    return line


def _ensure_env_loaded(workspace: Path) -> None:
    """Load <workspace>/.env if present; exported variables win and a repeat call is a no-op."""
    from dotenv import load_dotenv

    load_dotenv(Path(workspace) / ".env")
    # The entry point declares where the silent-model roster lives.
    ensure_roster_home(Path(workspace))


# ── main tick ─────────────────────────────────────────────────────────────────

def _charter_goal_router(workspace: Path) -> Any:
    """Router for the agent's own goal pick (charter or drives), with spend and cap accounting."""
    from app.bootstrap import (
        DEFAULT_BUDGET_LEDGER_PATH,
        DEFAULT_MODEL_USAGE_PATH,
    )
    from core.budget_ledger import BudgetLedger
    from core.model_router import ModelRouter
    from core.model_usage import ModelUsageLedger

    # Same pairing as app/bootstrap.py: the cap applies only when the usage ledger carries a
    # budget_ledger (reserve and cost recording are skipped without one).
    budget_ledger = BudgetLedger.from_env(
        path=workspace / DEFAULT_BUDGET_LEDGER_PATH,
        config_path=workspace / "config" / "budget_limits.json",
    )
    return ModelRouter.from_env(
        usage_ledger=ModelUsageLedger.from_env(
            path=workspace / DEFAULT_MODEL_USAGE_PATH,
            budget_ledger=budget_ledger,
        ),
    )


def _free_stranded_rows(
    task_store: Any, *, lock: Any, workspace: Path, dry_run: bool = False
) -> None:
    """Startup only, under the lock: free orphaned rows and resumable paused work.

    A dry run still re-queues an orphan with attempts left but never finalises one as failed.
    """
    from core.task_lifecycle import reactivate_resumable_work, recover_orphaned_tasks

    def _summarise(tasks: list) -> list[dict]:
        return [{"id": t.id, "status": t.status,
                 "attempts": t.attempts, "error": t.last_error} for t in tasks]

    for event, error_event, action, extra in (
        ("tasks_recovered", "task_recovery_error", recover_orphaned_tasks,
         {"finalise_exhausted": not dry_run}),
        ("paused_work_reactivated", "task_reactivation_error",
         reactivate_resumable_work, {}),
    ):
        try:
            freed = action(task_store, lock=lock, **extra)
        except Exception as exc:  # noqa: BLE001 — one pass failing must not
            _log_tick(workspace, {  # stop the other, nor the tick
                "event": error_event, "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        if freed:
            _log_tick(workspace, {"event": event, "count": len(freed),
                                  "dry_run": dry_run,
                                  "tasks": _summarise(freed)})


def _sweep_episodic_duplicates(workspace: Path, *, dry_run: bool = False) -> int:
    """Collapse byte-identical episodes (newest kept); return how many rows were dropped.

    The one CLI-only maintenance action allowed unattended, because it is mechanical; a dry
    run still counts and journals what would be dropped.
    """
    try:
        from core.episodic_hygiene import collapse_duplicate_episodes
        from core.memory_hygiene_commands import cited_episode_ids
        from core.smart_memory import EpisodicMemoryStore, ProceduralMemoryStore

        store = EpisodicMemoryStore(path=workspace / DATA_DIR / "episodic_memory.jsonl")
        # Episodes that procedures cite as credit are never collapsed away.
        procs = ProceduralMemoryStore(workspace / DATA_DIR / "procedural_memory.jsonl")
        dropped = collapse_duplicate_episodes(
            store, dry_run=dry_run, keep_ids=cited_episode_ids(procs, store),
        )
        if dropped:
            _log_tick(workspace, {
                "event": "episodic_duplicates_collapsed",
                "count": len(dropped),
                "dry_run": dry_run,
                "applied": not dry_run,
            })
        return len(dropped)
    except Exception as exc:  # noqa: BLE001 — hygiene must not cost the tick its work
        _log_tick(workspace, {"event": "episodic_sweep_error",
                              "error": f"{type(exc).__name__}: {exc}"})
        return 0


class BudgetConfigMissing(RuntimeError):
    """A live tick started where no spending cap is configured at all."""


def _require_budget_config(workspace: Path) -> None:
    """Refuse an unattended tick when the budget limits file is missing (zero limits mean none).

    Called at process entry, not in run_tick or the ledger, which tests use without the file.
    """
    named = os.environ.get("AGENT_BUDGET_CONFIG_PATH", "").strip()
    path = Path(named) if named else workspace / BUDGET_CONFIG_PATH
    if path.exists():
        return
    raise BudgetConfigMissing(
        f"budget limits file not found at {path}. An unattended tick refuses "
        f"to run without a spending cap: with no file and no AGENT_BUDGET_* "
        f"limits, every window's limit is zero, which reads as NO limit rather "
        f"than as no spending. Restore the file or point "
        f"AGENT_BUDGET_CONFIG_PATH at it."
    )


def _take_daily_snapshot(workspace: Path) -> None:
    """Take today's state snapshot unless it exists; called every tick, never breaks it."""
    try:
        from scripts.snapshot_state import take_snapshot

        target, copied, pruned = take_snapshot(workspace)
    except Exception as exc:  # noqa: BLE001 — a backup must never break the tick
        _log_tick(workspace, {"event": "state_snapshot_failed",
                              "error": f"{type(exc).__name__}: {exc}"[:200]})
        return
    if target is not None:
        _log_tick(workspace, {"event": "state_snapshot",
                              "path": str(target), "files": copied,
                              "pruned": pruned})


def run_tick(workspace: Path, *, dry_run: bool = True) -> int:
    """Execute one daemon tick. Returns exit code (0 = ok, 1 = hard error)."""
    _ensure_env_loaded(workspace)
    _take_daily_snapshot(workspace)

    # Full-suite test budget read by RunTestsTool (basis: tools/run_tests.py).
    os.environ.setdefault("AGENT_TEST_TIMEOUT_SECONDS", "900")

    # Lazy import after dotenv so env vars are available
    from app.bootstrap import build_agent
    from app.single_instance import AlreadyRunningError, SingleInstanceLock
    from core.approval_inbox import ApprovalInbox
    from core.autonomous_runtime import AutonomousRuntime, _config_from_task
    from core.incident import IncidentLog
    from core.scheduler import SchedulerStore
    from core.task_lifecycle import (
        apply_run_exception,
        apply_run_outcome,
        task_heartbeat,
    )
    from core.task_queue import TaskAlreadyClaimed, TaskQueueStore

    tick_start = _now_iso()
    summary: dict = {
        "tick_start": tick_start,
        "workspace": str(workspace),
        "dry_run": dry_run,
        "schedules_due": 0,
        "tasks_enqueued": 0,
        "tasks_processed": 0,
        "tests_result": None,
        "tests_health": "none",
        # Per-task test verdict, separate from run_status; a timed-out run is "inconclusive".
        "result_status": "none",
        "repair_proposed": False,
        "self_build_status": "none",
        "inbox_pending_after": 0,
        "error": None,
    }

    # Read the previous heartbeat before it is overwritten, so the dry-run streak carries over.
    _prev_hb = _read_heartbeat(workspace)
    try:
        _prev_streak = max(0, int((_prev_hb or {}).get("dry_run_streak", 0) or 0))
    except (TypeError, ValueError):
        _prev_streak = 0
    # No work yet: carry the streak; tick_complete recomputes it.
    visibility = _dry_run_visibility(
        dry_run=dry_run,
        previous_streak=_prev_streak,
        processed_effects=0,  # no effect is ever applied in this layer
        did_work=False,
    )
    summary.update(visibility)

    # Heartbeat before any work: liveness is recorded even if the tick crashes later.
    _write_heartbeat(
        workspace,
        {"event": "tick_start", "tick_start": tick_start, "dry_run": dry_run,
         **visibility},
    )

    # Bound before the try so the error path can always release it.
    _consumer_lock: SingleInstanceLock | None = None
    try:
        # ── 0. Budget kill-switch gate ────────────────────────────────────────
        # Day budget spent: skip all model work, checked before anything is built.
        kill_switch = _check_budget_kill_switch(workspace)
        if kill_switch.active:
            ks_payload = kill_switch.to_dict()
            summary["error"] = None
            summary["budget_kill_switch"] = ks_payload
            summary["result_status"] = "budget_kill_switch"
            summary["tick_end"] = _now_iso()
            _log_tick(workspace, {"event": "budget_kill_switch", **ks_payload})
            _write_heartbeat(
                workspace,
                {
                    "event": "budget_kill_switch",
                    "tick_start": tick_start,
                    "dry_run": dry_run,
                    **ks_payload,
                    **visibility,
                },
            )
            print(
                "[agent_tick] budget kill-switch active "
                f"({kill_switch.counter} {kill_switch.used}/{kill_switch.limit}); "
                "skipping LLM-heavy work",
                file=sys.stderr,
            )
            return 0

        # ── 1. Scheduler tick — enqueue due schedules ─────────────────────────
        sched_store = SchedulerStore(workspace / SCHEDULES_PATH)
        task_store  = TaskQueueStore(workspace / TASK_QUEUE_PATH)
        tick_report = sched_store.tick(task_queue=task_store)
        summary["schedules_due"]   = tick_report.due_count
        summary["tasks_enqueued"]  = tick_report.enqueued_count
        _log_tick(workspace, {"event": "scheduler_tick", **tick_report.to_dict()})

        # ── 2. Claim and run pending tasks ────────────────────────────────────
        # Drain under the single-instance lock (no double claims, safe startup recovery);
        # if another consumer holds it, skip the drain and do the rest of the tick.
        _consumer_lock = SingleInstanceLock(workspace / DAEMON_LOCK_PATH)
        try:
            _consumer_lock.acquire()
        except AlreadyRunningError as exc:
            _consumer_lock = None
            summary["task_drain"] = "skipped_locked"
            _log_tick(workspace, {"event": "task_drain_skipped",
                                  "reason": "another consumer holds the lock",
                                  "details": getattr(exc, "details", {})})

        pending_tasks = []
        if _consumer_lock is not None:
            _free_stranded_rows(task_store, lock=_consumer_lock,
                                workspace=workspace, dry_run=dry_run)
            # Same slot: the unattended path produces the most duplicate episodes.
            _sweep_episodic_duplicates(workspace, dry_run=dry_run)
            # pending() honours run_after, so a task in retry backoff is not re-run at once.
            pending_tasks = task_store.pending()

        if not pending_tasks:
            if _consumer_lock is not None:
                _log_idle_tick(workspace)
            # Still check approval inbox below
        else:
            agent = build_agent(
                workspace, approval_provider=None,
                **unattended_memory_profile(workspace),
            )
            inbox = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
            incident_log = IncidentLog(path=workspace / INCIDENT_LOG_PATH)
            runtime = AutonomousRuntime(
                agent,
                workspace=workspace,
                approval_inbox=inbox,
                incident_log=incident_log,
                receipt_path="daemon",
            )

            for task in pending_tasks:
                try:
                    task_store.mark_running(task.id)
                except TaskAlreadyClaimed:
                    _log_tick(workspace, {"event": "task_claim_lost",
                                          "task_id": task.id})
                    continue
                except KeyError:
                    _log_tick(workspace, {"event": "task_vanished",
                                          "task_id": task.id})
                    continue

                config = _config_from_task(task)
                # Always honour dry_run flag from CLI / env
                import dataclasses
                if dry_run and not config.dry_run:
                    config = dataclasses.replace(config, dry_run=True)

                try:
                    # Heartbeat during the run: recovery tells a killed process from a slow one.
                    with task_heartbeat(task_store, task.id):
                        run_report = runtime.run(config)
                except Exception as exc:   # noqa: BLE001
                    # Settle the task here, or it stays `running` with nothing to recover it.
                    failed, decision = apply_run_exception(
                        task_store, task.id, exc
                    )
                    summary["tasks_processed"] += 1
                    summary["result_status"] = "failed"
                    _log_tick(workspace, {
                        "event": "task_failed",
                        "task_id": task.id,
                        "status": failed.status,
                        "error_type": type(exc).__name__,
                        **decision.to_log_payload(),
                    })
                    continue

                summary["tasks_processed"] += 1

                # Per-task verdict, distinct from run_status.
                task_result_status = "none"

                # Record test outcomes
                for task_report in run_report.tasks:
                    if task_report.status == "clarify":
                        summary["clarification"] = task_report.details.get("clarification")
                        summary["clarification_stop_reason"] = task_report.details.get(
                            "stop_reason", "replan_exhausted"
                        )
                        task_result_status = "clarify"
                        summary["result_status"] = "clarify"
                        _log_tick(
                            workspace,
                            {
                                "event": "clarification_required",
                                "task_id": task.id,
                                "stop_reason": summary["clarification_stop_reason"],
                                "clarification": summary["clarification"],
                            },
                        )
                    if task_report.task.kind == "tests":
                        summary["tests_result"] = {
                            "status": task_report.status,
                            "summary": task_report.summary,
                            **task_report.details,
                        }
                        summary["tests_health"] = _classify_test_health(
                            summary["tests_result"]
                        )
                        # The runtime already classified it (done/failed/inconclusive).
                        task_result_status = task_report.status
                        summary["result_status"] = task_report.status
                        if task_report.status == "failed":
                            repair_result = _maybe_propose_repair(
                                workspace, task_report.details, inbox, agent
                            )
                            summary["repair_proposed"] = repair_result.get(
                                "repair_proposed", False
                            )
                            _log_tick(workspace, {"event": "repair_attempt", **repair_result})
                        elif summary["tests_health"] == "inconclusive":
                            # No repair (no failing test to target), but never silently healthy.
                            _log_tick(
                                workspace,
                                {
                                    "event": "tests_inconclusive",
                                    "reason": task_report.summary,
                                    "details": task_report.details,
                                },
                            )

                # Shared with the CLI/campaign consumer: completed -> done, approval-blocked ->
                # blocked, stopped -> failed with the stop reason.
                updated, decision = apply_run_outcome(
                    task_store,
                    task.id,
                    status=run_report.status,
                    stop_reason=run_report.stop_reason,
                    report=run_report.to_dict(),
                )
                # Three axes kept apart: run_status (did it finish), result_status (what it
                # established), task_status (where the queue row ended up).
                _log_tick(workspace, {"event": "task_done", "task_id": task.id,
                                      "run_status": run_report.status,
                                      "task_status": updated.status,
                                      "lifecycle_reason": decision.reason,
                                      "result_status": task_result_status,
                                      "mode": summary["mode"],
                                      "effects": summary["effects"],
                                      "processed_effects": summary["processed_effects"]})

        # Drained: release the lock before the slower producer work (nothing below claims tasks).
        if _consumer_lock is not None:
            _consumer_lock.release()
            _consumer_lock = None

        # ── 3. Autonomous self-build producer ─────────────────────────────────
        # At most one proposal per tick, into the human-gated inbox; it never applies anything.
        producer_inbox = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
        self_build = _maybe_produce_self_build(
            workspace, producer_inbox, dry_run=dry_run
        )
        summary["self_build_status"] = self_build.get("self_build_status", "none")
        summary["self_build"] = self_build
        _log_tick(workspace, {"event": "self_build_produce", **self_build})

        # ── 4. Tally inbox ────────────────────────────────────────────────────
        inbox_check = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
        summary["inbox_pending_after"] = len(inbox_check.pending())

    except Exception as exc:  # noqa: BLE001
        # Release before reporting: run_tick also runs in-process, so process exit is no help.
        if _consumer_lock is not None:
            try:
                _consumer_lock.release()
            except Exception:  # noqa: BLE001, S110 — the OS frees this lock at exit either way
                pass
            _consumer_lock = None
        summary["error"] = f"{type(exc).__name__}: {exc}"
        _log_tick(workspace, {"event": "tick_error", "error": summary["error"],
                              "traceback": traceback.format_exc()})
        _write_heartbeat(
            workspace,
            {
                "event": "tick_error",
                "tick_start": tick_start,
                "dry_run": dry_run,
                "error": summary["error"],
                **visibility,
            },
        )
        print(f"[agent_tick] ERROR: {exc}", file=sys.stderr)
        return 1

    summary["tick_end"] = _now_iso()
    # Recompute the streak now that did_work is known.
    visibility = _dry_run_visibility(
        dry_run=dry_run,
        previous_streak=_prev_streak,
        processed_effects=0,
        did_work=summary["tasks_processed"] > 0,
    )
    summary.update(visibility)

    # ── Memory maintenance ───────────────────────────────────────────────────
    # AGENT_AUTO_HYGIENE: shadow (default, count only), on (deletes, live ticks only), off.
    # Runs after the summary so maintenance can never change the tick's verdict.
    _hygiene_mode = os.environ.get("AGENT_AUTO_HYGIENE", "shadow").strip().lower()
    if _hygiene_mode in {"shadow", "on"}:
        try:
            _hyg_agent = build_agent(
                workspace, approval_provider=None,
                **unattended_memory_profile(workspace),
            )
            _hyg_shadow = dry_run or _hygiene_mode == "shadow"
            _hyg = _hyg_agent.run_maintenance_pass(dry_run=_hyg_shadow)
            _log_tick(workspace, {"event": "maintenance_pass",
                                  "mode": _hygiene_mode,
                                  "tick_dry_run": dry_run, **_hyg})
        except Exception as exc:  # noqa: BLE001
            _log_tick(workspace, {"event": "maintenance_error",
                                  "error": type(exc).__name__})

    try:
        from core.rule_approved_apply import drain_rule_approved_proposals

        summary["rule_approved"] = drain_rule_approved_proposals(
            workspace, dry_run=dry_run,
            log=lambda e, p: _log_tick(workspace, {"event": e, **p}),
            # One source of truth for sinks: the unattended agent's own memory profile.
            durable_writes=unattended_memory_profile(workspace)["durable_writes"],
        )
    except Exception as exc:  # noqa: BLE001 — closing the loop must not break the tick
        _log_tick(workspace, {"event": "rule_approval_error",
                              "error": type(exc).__name__})

    _log_tick(workspace, {"event": "tick_complete", **summary})

    # Heartbeat on completion, with the health verdict.
    _write_heartbeat(
        workspace,
        {
            "event": "tick_complete",
            "tick_start": tick_start,
            "tick_end": summary["tick_end"],
            "dry_run": dry_run,
            "tests_health": summary["tests_health"],
            "result_status": summary["result_status"],
            "tasks_processed": summary["tasks_processed"],
            "inbox_pending_after": summary["inbox_pending_after"],
            "self_build_status": summary["self_build_status"],
            "self_build_next_human_action": (
                summary.get("self_build", {}).get("next_human_action", "")
            ),
            "self_build_approval_id": (
                summary.get("self_build", {}).get("approval_id")
            ),
            **visibility,
        },
    )

    # Human-readable summary to stderr (captured by Task Scheduler logs)
    pending = summary["inbox_pending_after"]
    tests   = summary["tests_result"]
    health  = summary["tests_health"]
    # Greppable mode line; never implies effects ran.
    print(
        f"[agent_tick] mode={summary['mode']} effects={summary['effects']} "
        f"processed_effects={summary['processed_effects']} "
        f"dry_run_streak={summary['dry_run_streak']}",
        file=sys.stderr,
    )
    if tests:
        print(
            f"[agent_tick] tests_health={health} "
            f"result_status={summary['result_status']} "
            f"({tests.get('summary','')})",
            file=sys.stderr,
        )
        if health == "inconclusive":
            print(
                "[agent_tick] WARNING: test run was inconclusive "
                "(timeout / no exit code / nothing collected) — "
                "NOT treating this tick as healthy.",
                file=sys.stderr,
            )
    if summary.get("clarification"):
        clar = summary["clarification"]
        print(
            "[agent_tick] clarification required "
            f"({summary.get('clarification_stop_reason', 'replan_exhausted')}):",
            file=sys.stderr,
        )
        for question in clar.get("questions") or []:
            print(f"  - {question}", file=sys.stderr)
        forbidden = clar.get("forbidden_actions") or []
        if forbidden:
            print(
                f"  forbidden while unclear: {', '.join(forbidden)}",
                file=sys.stderr,
            )
    if summary["repair_proposed"]:
        print("[agent_tick] Repair proposal added to inbox.", file=sys.stderr)
    sb = summary.get("self_build", {})
    sb_status = summary["self_build_status"]
    if sb_status == "proposed":
        print(
            f"[agent_tick] Self-build proposal added to inbox "
            f"({sb.get('approval_id')}). {sb.get('next_human_action', '')}",
            file=sys.stderr,
        )
    elif sb_status not in ("none", "cooldown_wait"):
        print(_self_build_status_line(sb_status, sb), file=sys.stderr)
    if pending:
        print(f"[agent_tick] {pending} item(s) waiting in approval inbox.", file=sys.stderr)
    else:
        print("[agent_tick] Inbox clear.", file=sys.stderr)

    return 0


# ── paced campaign daemon mode ────────────────────────────────────────────────

def _pick_next_charter_goal(workspace: Path) -> Any:
    """Return the pick report (goal and success check), or "" when there is no goal."""
    try:
        from core.charter_goal import propose_charter_goal
        router = _charter_goal_router(workspace)
        pick = propose_charter_goal(router.for_role("planner"), workspace)
    except Exception as exc:  # noqa: BLE001 — a failed goal switch must not crash the run
        print(f"[CHARTER] next goal failed: {type(exc).__name__}: {exc}")
        return ""
    if pick.status != "proposed":
        print(f"[CHARTER] no next goal: {pick.reason}")
        return ""
    print(f"[CHARTER] next goal: {pick.goal}")
    print(f"[CHARTER] anchored to: {pick.charter_quote!r}")
    print(f"[CHARTER] success check: {pick.success_check}")
    return pick


# How long to wait for a need to cross the threshold: drives grow with time, so an empty
# pick is not the end (topic drives need about ten minutes to cross it again).
_NEED_WAIT_SECONDS = 20 * 60


def _wait_for_a_need(workspace: Path, *, pause_seconds: float,
                     budget_seconds: float = _NEED_WAIT_SECONDS,
                     sleep_fn: Any = time.sleep, now_fn: Any = time.monotonic) -> Any:
    """First goal from the drives; an empty pick means wait, not stop."""
    from core.campaign import goal_pick_attempts

    deadline = now_fn() + max(0.0, budget_seconds)
    waited = False
    while True:
        for _attempt in range(goal_pick_attempts()):
            pick = _pick_next_drive_goal(workspace)
            if pick:
                return pick
        if now_fn() >= deadline:
            return None
        if not waited:
            print(f"[DRIVES] all needs below threshold; waiting up to {int(budget_seconds)}s "
                  "for one to grow")
            waited = True
        sleep_fn(max(1.0, float(pause_seconds)))


def _pick_next_drive_goal(workspace: Path) -> Any:
    """Next goal from the strongest drive (core/drive_goal.py)."""
    try:
        from core.drive_goal import propose_drive_goal
        pick = propose_drive_goal(_charter_goal_router(workspace).for_role("planner"), workspace)
    except Exception as exc:  # noqa: BLE001 — a goal switch never crashes the run
        print(f"[DRIVES] next goal failed: {type(exc).__name__}: {exc}")
        return ""
    if pick.status != "proposed":
        print(f"[DRIVES] no next goal ({pick.drive}): {pick.reason}")
        return ""
    print(f"[DRIVES] {pick.drive} -> {pick.goal}")
    return pick


def run_paced_campaign(
    workspace: Path,
    *,
    dry_run: bool = True,
    goal: str = "project health",
    success_check: str = "",
    max_cycles: int = 24,
    cycle_pause_seconds: int = 0,
    max_wall_clock_seconds: int = 0,
    max_llm_calls: int = 100,
    max_cost_units: int = 0,
    max_unproductive_streak: int = 3,
    max_goal_switches: int = 12,
    pursue_goal_when_idle: bool = False,
    opening_spend: tuple[int, int] = (0, 0),
    heartbeat_fn: Callable[[Path, dict], None] | None = None,
    charter_goals: bool = False,
    drive_goals: bool = False,
    goal_action: str = "",
    run_campaign_fn: Callable[..., Any] | None = None,
    build_agent_fn: Callable[[Path], Any] | None = None,
    drain_fn: Callable[..., Any] | None = None,
) -> int:
    """Run one long, paced campaign in this process, with a heartbeat per cycle.

    Unlike run_tick it drives the cycles itself over wall-clock time; dry run by default, and
    effects still pass the campaign's approval gate. Returns 0 (ok) or 1 (hard error).
    """
    from core.campaign import (
        CampaignConfig,
        CampaignLedger,
    )
    from core.campaign import (
        run_campaign as _real_run_campaign,
    )

    write_heartbeat = heartbeat_fn or _write_heartbeat
    run_campaign = run_campaign_fn or _real_run_campaign
    drain = drain_fn or drain_and_log

    def _on_cycle(snapshot: dict) -> None:
        # Best effort: a heartbeat write failure must never kill a multi-hour campaign.
        payload = {
            "event": "campaign_cycle",
            "dry_run": dry_run,
            "mode": "dry_run" if dry_run else "live",
            "effects": "disabled" if dry_run else "enabled",
            **snapshot,
        }
        try:
            write_heartbeat(workspace, payload)
        except (OSError, TypeError, ValueError):
            pass
        # Drain approved proposals after every cycle, not once at the end; it never raises.
        if not dry_run:
            drain(
                workspace, dry_run=dry_run,
                log_tick=lambda p: _log_tick(workspace, p),
                durable_writes=unattended_memory_profile(workspace)["durable_writes"],
            )

    try:
        config = CampaignConfig(
            goal=goal,
            success_check=success_check,
            dry_run=dry_run,
            max_cycles=max_cycles,
            max_llm_calls=max_llm_calls,
            max_cost_units=max_cost_units,
            cycle_pause_seconds=cycle_pause_seconds,
            max_wall_clock_seconds=max_wall_clock_seconds,
            max_unproductive_streak=max_unproductive_streak,
            max_goal_switches=max_goal_switches,
            pursue_goal_when_idle=pursue_goal_when_idle,
            # --goal is the human's goal, drives and charter give the agent's own (GOAL_GROUNDS).
            # Drive and human goals go before the agent's own task menu.
            goal_is_self=bool(drive_goals or charter_goals),
            **({"max_idle_streak": 1, "goal_first": True, "goal_action": goal_action}
               if drive_goals else {} if charter_goals else {"goal_first": True}),
        )
    except ValueError as exc:
        print(f"[agent_tick] campaign config error: {exc}", file=sys.stderr)
        return 1

    # Heartbeat before any cycle: the campaign process has started.
    write_heartbeat(workspace, {
        "event": "campaign_start",
        "dry_run": dry_run,
        "mode": "dry_run" if dry_run else "live",
        "effects": "disabled" if dry_run else "enabled",
        "processed_effects": 0,
        "goal": config.goal,
        "max_cycles": config.max_cycles,
        "cycle_pause_seconds": config.cycle_pause_seconds,
        "max_wall_clock_seconds": config.max_wall_clock_seconds,
    })

    # Build the agent + inbox + ledger lazily (mirrors run_tick).
    if build_agent_fn is not None:
        agent = build_agent_fn(workspace)
    else:
        from dotenv import load_dotenv
        load_dotenv(workspace / ".env")
        ensure_roster_home(workspace)  # silent-model roster home
        from app.bootstrap import build_agent
        agent = build_agent(
            workspace, approval_provider=None,
            **unattended_memory_profile(workspace),
        )

    from core.approval_inbox import ApprovalInbox
    inbox = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
    ledger = CampaignLedger(path=workspace / DATA_DIR / "campaign_ledger.jsonl")

    print(
        f"[agent_tick] paced campaign goal={config.goal!r} dry_run={dry_run} "
        f"max_cycles={config.max_cycles} pause={config.cycle_pause_seconds}s "
        f"ceiling={config.max_wall_clock_seconds}s",
        file=sys.stderr,
    )

    # next_goal replaces an exhausted goal inside the run through the same gate as at the start;
    # a refusal ends the run honestly.
    def _call_run_campaign(**extra):
        # Drop kwargs an older run_campaign lacks; by signature, not TypeError, so an error
        # raised inside the campaign never runs it twice.
        try:
            params = inspect.signature(run_campaign).parameters
        except (TypeError, ValueError):
            params = None
        if params is not None and not any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        ):
            extra = {k: v for k, v in extra.items() if k in params}
        return run_campaign(
            config, agent=agent, workspace=workspace,
            approval_inbox=inbox, ledger=ledger, on_cycle=_on_cycle,
            **extra,
        )

    try:
        result = _call_run_campaign(
            opening_spend=opening_spend,
            **({"next_goal": lambda: _pick_next_drive_goal(workspace)} if drive_goals
               else {"next_goal": lambda: _pick_next_charter_goal(workspace)} if charter_goals else {}),
        )
    except Exception as exc:  # noqa: BLE001 — the failure lands in the heartbeat
        write_heartbeat(workspace, {
            "event": "campaign_error",
            "dry_run": dry_run,
            "error": f"{type(exc).__name__}: {exc}",
        })
        print(
            f"[agent_tick] campaign failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    # Final heartbeat and tick-log summary.
    write_heartbeat(workspace, {
        "event": "campaign_complete",
        "dry_run": dry_run,
        "mode": "dry_run" if dry_run else "live",
        "effects": "disabled" if dry_run else "enabled",
        "processed_effects": 0,
        "status": result.status,
        "stop_reason": result.stop_reason,
        "cycles_run": result.cycles_run,
        **result.totals,
    })
    _log_tick(workspace, {
        "event": "campaign_complete",
        "status": result.status,
        "stop_reason": result.stop_reason,
        "cycles_run": result.cycles_run,
        "totals": result.totals,
    })
    drain(
        workspace, dry_run=dry_run,
        log_tick=lambda p: _log_tick(workspace, p),
        durable_writes=unattended_memory_profile(workspace)["durable_writes"],
    )
    print(result.user_summary(), file=sys.stderr)
    return 0


# ── entry point ───────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autonomous agent daemon tick.")
    parser.add_argument(
        "--workspace",
        default=str(WORKSPACE_DEFAULT),
        help="Path to workspace root (default: directory of this script).",
    )
    parser.add_argument(
        "--allow-effects",
        action="store_true",
        help="Disable dry-run; allow the runtime to write files (use with care).",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Only print pending inbox items and exit; do not run a tick.",
    )
    parser.add_argument(
        "--campaign",
        action="store_true",
        help="Run a long PACED campaign in this process (heartbeat per cycle) "
             "instead of one bounded tick. Use with the pacing flags below.",
    )
    parser.add_argument(
        "--goal",
        default="project health",
        help="Campaign goal (only used with --campaign).",
    )
    parser.add_argument(
        "--charter",
        action="store_true",
        help="Let the agent pick its own campaign goal from the charter "
             "(knowledge/doctrine/future/CORPORATE_MODEL.md) instead of --goal. "
             "A declined pick exits with the named gate, not a fallback goal.",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=24,
        help="Max campaign cycles (only used with --campaign).",
    )
    parser.add_argument(
        "--cycle-pause-seconds",
        type=int,
        default=0,
        help="Real wall-clock pause between cycles (only used with --campaign).",
    )
    parser.add_argument(
        "--max-wall-clock-seconds",
        type=int,
        default=0,
        help="Hard real-time ceiling for the campaign (only used with --campaign).",
    )
    parser.add_argument(
        "--max-llm-calls",
        type=int,
        default=100,
        help="Campaign llm-call budget, 0 = unlimited (only used with --campaign).",
    )
    parser.add_argument(
        "--max-cost-units",
        type=int,
        default=0,
        help="Campaign cost-unit budget, 0 = unlimited (only used with --campaign).",
    )
    parser.add_argument(
        "--max-unproductive-streak",
        type=int,
        default=3,
        help="Stop the campaign after this many consecutive executed cycles "
             "with no useful state change (loop_suspected); 0 = off "
             "(only used with --campaign).",
    )
    parser.add_argument(
        "--drives",
        action="store_true",
        help="Campaign goals come from the agent's measured drives (core/drives.py, "
             "core/drive_goal.py) instead of the charter (only used with --campaign).",
    )
    parser.add_argument(
        "--pursue-goal-when-idle",
        action="store_true",
        help="When no menu action binds the chosen goal, work on the goal itself "
             "(one agent pass per goal) instead of idling (only used with --campaign).",
    )
    parser.add_argument(
        "--max-goal-switches",
        type=int,
        default=12,
        help="How many times the campaign may replace an exhausted goal with a "
             "new self-chosen one; 0 = unlimited (only used with --campaign).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    ws   = Path(args.workspace).resolve()

    if args.status:
        sys.exit(_print_status(ws))

    _require_budget_config(ws)

    dry = not args.allow_effects
    # Respect env var override too
    if os.environ.get("AGENT_TICK_DRY_RUN", "1").strip().lower() in {"0", "false", "no"}:
        dry = False

    goal = args.goal
    # Goal success check; "" means none named (a --goal arrives without one).
    success_check = ""
    opening_spend = (0, 0)
    if args.campaign and args.charter:
        # The agent picks its goal from the charter; a refusal exits with the named gate. Load
        # .env here: run_tick and the pacer load it later, and the pick would use the default model.
        _ensure_env_loaded(ws)
        from core.charter_goal import propose_charter_goal

        _charter_router = _charter_goal_router(ws)
        # The opening pick is paid for here, before the campaign; spend covers all attempts.
        from core.campaign_io import router_spend, spend_since
        _spend_before = router_spend(_charter_router)
        # Several attempts at the start; each failed one leaves a stop record and an observation.
        pick = None
        from core.campaign import goal_pick_attempts
        attempts = goal_pick_attempts()
        for _attempt in range(attempts):
            pick = propose_charter_goal(_charter_router.for_role("planner"), ws)
            if pick.status == "proposed":
                break
            print(f"[CHARTER] no goal (attempt {_attempt + 1}"
                  f"/{attempts}): {pick.reason}")
            from core.self_stop_record import (
                reason_kind,
                record_self_stop,
                record_stop_observation,
            )
            _wall = reason_kind(pick.reason)
            _stop = record_self_stop(
                kind="goal_selection_failure",
                source="data/charter_decisions.jsonl",
                reason=_wall,
                ts=datetime.now(timezone.utc).isoformat(),
                outcome=pick.status,
                workspace=ws,
            )
            record_stop_observation(
                ws,
                kind="goal_selection_failure",
                reason=_wall,
                signature=str(_stop.get("signature") or ""),
                source="data/charter_decisions.jsonl",
            )
        if pick is None or pick.status != "proposed":
            print("[CHARTER] no goal after "
                  f"{attempts} attempts; stopping honestly")
            sys.exit(3)
        print(f"[CHARTER] goal: {pick.goal}")
        print(f"[CHARTER] anchored to: {pick.charter_quote!r}")
        print(f"[CHARTER] success check: {pick.success_check}")
        goal = pick.goal
        opening_spend = spend_since(_charter_router, _spend_before)
        success_check = pick.success_check

    _first_action = ""
    if args.campaign and args.drives:
        _ensure_env_loaded(ws)
        _first = _wait_for_a_need(ws, pause_seconds=args.cycle_pause_seconds or 30)
        if not _first:
            print("[DRIVES] no need grew within the waiting window; stopping honestly")
            sys.exit(3)
        goal, success_check = _first.goal, _first.success_check
        _first_action = str(getattr(_first, "action", "") or "")

    if args.campaign:
        sys.exit(run_paced_campaign(
            ws,
            dry_run=dry,
            goal=goal,
            success_check=success_check,
            max_cycles=args.max_cycles,
            cycle_pause_seconds=args.cycle_pause_seconds,
            max_wall_clock_seconds=args.max_wall_clock_seconds,
            max_llm_calls=args.max_llm_calls,
            max_cost_units=args.max_cost_units,
            max_unproductive_streak=args.max_unproductive_streak,
            max_goal_switches=args.max_goal_switches,
            pursue_goal_when_idle=args.pursue_goal_when_idle,
            opening_spend=opening_spend,
            charter_goals=bool(args.charter),
            drive_goals=bool(args.drives),
            goal_action=_first_action if args.drives else "",
        ))

    sys.exit(run_tick(ws, dry_run=dry))
