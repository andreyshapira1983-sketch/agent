"""Autonomous agent daemon tick.

This script is designed to be called by Windows Task Scheduler (or any cron
equivalent) on a fixed interval — e.g. every 30 minutes. It does NOT stay
running; it wakes up, does one bounded health pass, writes results to disk,
and exits. Zero risk of runaway processes.

What one tick does
------------------
1. Load scheduler store → find schedules that are due.
2. Enqueue due schedules as RuntimeTasks.
3. Claim pending RuntimeTasks → run AutonomousRuntime (dry-run by default).
4. If any pytest tests FAILED → call RepairProposalGenerator → put result
   in ApprovalInbox so the human sees it next time they open the REPL.
5. Write a tick summary to logs/daemon_tick.jsonl.
6. Exit with code 0 (success) or 1 (hard error).

Environment variables
---------------------
AGENT_WORKSPACE   path to workspace root (default: directory of this file)
AGENT_PROVIDER    mock | openai | anthropic  (default: mock for safety)
AGENT_TICK_DRY_RUN  1 = never write real files, 0 = allow effects (default: 1)

Usage
-----
python agent_tick.py                    # run once, workspace = .
python agent_tick.py --workspace C:/x   # explicit workspace
python agent_tick.py --allow-effects    # disable dry-run (use with care)
python agent_tick.py --status           # print pending inbox items and exit

Windows Task Scheduler quick-start
-----------------------------------
Run scripts/install_daemon.ps1 to register the scheduled task automatically.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


# ── paths ─────────────────────────────────────────────────────────────────────

WORKSPACE_DEFAULT = Path(os.environ.get("AGENT_WORKSPACE", Path(__file__).parent)).resolve()

DATA_DIR           = "data"
LOGS_DIR           = "logs"
TICK_LOG_FILE      = "daemon_tick.jsonl"
APPROVAL_INBOX_PATH = "data/approval_inbox.jsonl"
SCHEDULES_PATH     = "data/runtime_schedules.jsonl"
TASK_QUEUE_PATH    = "data/task_queue.jsonl"
HEARTBEAT_PATH     = "data/daemon_heartbeat.json"

# Expected wall-clock gap between ticks. The daemon is normally driven by Task
# Scheduler every 30 minutes. If the newest heartbeat is older than
# STALENESS_FACTOR * this interval, the daemon is considered stale (likely not
# running) rather than merely idle. Override via AGENT_TICK_INTERVAL_SECONDS.
EXPECTED_TICK_INTERVAL_SECONDS = int(
    os.environ.get("AGENT_TICK_INTERVAL_SECONDS", "1800")
)
STALENESS_FACTOR = 2


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_tick(workspace: Path, payload: dict) -> None:
    log_path = workspace / LOGS_DIR / TICK_LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _now_iso(), **payload}, ensure_ascii=False) + "\n")


def _write_heartbeat(workspace: Path, payload: dict) -> None:
    """Persist the latest daemon liveness record (overwrites previous).

    A single small JSON file gives O(1) staleness checks without scanning the
    append-only tick log. Always stamped with the current time.
    """
    hb_path = workspace / HEARTBEAT_PATH
    hb_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": _now_iso(), **payload}
    tmp = hb_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    tmp.replace(hb_path)  # atomic swap


def _read_heartbeat(workspace: Path) -> dict | None:
    hb_path = workspace / HEARTBEAT_PATH
    if not hb_path.exists():
        return None
    try:
        return json.loads(hb_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _heartbeat_age_seconds(heartbeat: dict | None, *, now: datetime | None = None) -> float | None:
    """Return seconds since the heartbeat timestamp, or None if unavailable."""
    if not heartbeat:
        return None
    ts = heartbeat.get("ts")
    if not ts:
        return None
    try:
        when = datetime.fromisoformat(str(ts))
    except (ValueError, TypeError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - when).total_seconds())


def _is_stale(age_seconds: float | None) -> bool:
    """True when the daemon has not ticked within the staleness window."""
    if age_seconds is None:
        return True
    return age_seconds > EXPECTED_TICK_INTERVAL_SECONDS * STALENESS_FACTOR


def _dry_run_visibility(
    *,
    dry_run: bool,
    previous_streak: int = 0,
    processed_effects: int = 0,
    did_work: bool = True,
) -> dict:
    """Honest observability fields for the current tick — PURE, NO behaviour change.

    A pure function: no file/env/clock reads, no side effects. The caller is
    responsible for sourcing ``previous_streak`` (e.g. from the prior heartbeat)
    and ``processed_effects`` (count of effects actually applied this tick).

    This block only *describes* what the daemon is (not) doing; it never enables
    effects, never touches approval policy, and never alters proposal logic. It
    is also kept SEPARATE from run_status / result_status — those answer "did the
    run finish" and "what did it establish"; these answer "what mode am I in and
    did I apply anything".

    Args:
      - ``dry_run``          — whether this tick runs without applying effects.
      - ``previous_streak``  — dry_run_streak carried from the previous tick.
      - ``processed_effects``— effects actually applied this tick (normally 0).
      - ``did_work``         — whether this tick actually ran a dry-run pass
                               (``tasks_processed > 0``). An IDLE no-op tick
                               (nothing due, nothing pending) did no work and
                               carries no information, so it must NOT inflate the
                               streak. Defaults to ``True`` so callers that only
                               care about mode/effects keep the simple behaviour.

    Returns:
      - ``mode``             — ``"dry_run"`` or ``"live"``.
      - ``effects``          — ``"disabled"`` in dry-run, else ``"enabled"``
                               (i.e. *policy-allowed*, not "already applied").
      - ``processed_effects``— echoed back, clamped to a non-negative int. In
                               practice 0: dry-run applies nothing and the live
                               path is still gated into the approval inbox.
      - ``dry_run_streak``   — consecutive *meaningful* dry-run passes. On a
                               dry-run tick that did work it is ``previous_streak
                               + 1`` (regardless of test health — a failed /
                               inconclusive dry-run pass is STILL a dry-run pass).
                               On an IDLE dry-run tick (``did_work`` false) it is
                               carried forward UNCHANGED — an idle tick adds no
                               information and must not dilute the stall signal.
                               A live tick resets it to 0.
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


def _classify_test_health(tests_result: dict | None) -> str:
    """Map a ``tests_result`` payload to a single honest health verdict.

    Returns one of:
      - ``"none"``         — no tests ran this tick (nothing to claim).
      - ``"pass"``         — tests finished cleanly with at least one pass.
      - ``"fail"``         — at least one test failed or errored.
      - ``"inconclusive"`` — the run timed out, had no exit code, or collected
                             zero tests. This is the key fix: a timed-out run
                             must NEVER be read as ``"pass"`` just because its
                             failure count is zero.
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
        # exit_code present, zero failures, but nothing actually ran:
        # this is not evidence of health.
        return "inconclusive"
    return "pass"



# ── status-only mode ──────────────────────────────────────────────────────────

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
        else:
            print(
                f"Daemon: alive — last tick {age_min:.1f} min ago "
                f"(event={last_event}).",
                file=sys.stderr,
            )

    # Dry-run visibility from the last heartbeat: honest "what was (not) applied".
    if heartbeat is not None:
        mode = heartbeat.get("mode", "?")
        effects = heartbeat.get("effects", "?")
        processed = heartbeat.get("processed_effects", "?")
        streak = heartbeat.get("dry_run_streak", "?")
        print(
            f"Mode: {mode} (effects={effects}, processed_effects={processed}, "
            f"dry_run_streak={streak})",
            file=sys.stderr,
        )

    inbox = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
    pending = inbox.pending()
    if not pending:
        print("Inbox: no pending items.", file=sys.stderr)
        return 0
    print(f"Inbox: {len(pending)} pending item(s):", file=sys.stderr)
    for item in pending:
        print(f"  [{item.id}] {item.operation}: {item.summary}", file=sys.stderr)
    return 0


# ── repair proposal after failed tests ───────────────────────────────────────

def _repair_target_from_failures(failed_names: list[str], workspace: Path) -> str | None:
    """Pick a single concrete repair target from failing test names.

    `RepairProposalGenerator.generate` requires one existing `target_path`;
    it does NOT auto-pick a target. Each failing name looks like
    ``tests/foo.py::Klass::test_bar`` — the part before ``::`` is the test
    file. We return that file only when the failures point at exactly one
    existing file; otherwise we return None so the caller refuses cleanly
    instead of guessing across multiple files.
    """
    files: list[str] = []
    for name in failed_names:
        path = (name or "").split("::", 1)[0].strip()
        if not path:
            continue
        if (workspace / path).is_file() and path not in files:
            files.append(path)
    return files[0] if len(files) == 1 else None


def _maybe_propose_repair(
    workspace: Path,
    test_report: dict,
    inbox: "ApprovalInbox",
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
            # RepairProposal (core/self_repair.py) carries `path` / `reason` /
            # `proposed_content` — NOT target_file/description/patch_preview.
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

        return {"repair_proposed": False, "reason": f"generator status: {report.status}"}

    except Exception as exc:  # noqa: BLE001
        return {"repair_proposed": False, "reason": f"exception: {exc}"}


# ── main tick ─────────────────────────────────────────────────────────────────

def run_tick(workspace: Path, *, dry_run: bool = True) -> int:
    """Execute one daemon tick. Returns exit code (0 = ok, 1 = hard error)."""
    from dotenv import load_dotenv
    load_dotenv(workspace / ".env")

    # Give the test runner enough time for a full suite (1800+ tests).
    # RunTestsTool reads this env var if no explicit timeout_seconds was given.
    os.environ.setdefault("AGENT_TEST_TIMEOUT_SECONDS", "300")

    # Lazy import after dotenv so env vars are available
    from core.approval_inbox import ApprovalInbox
    from core.scheduler import SchedulerStore
    from core.task_queue import TaskQueueStore
    from core.autonomous_runtime import AutonomousRuntime, _config_from_task
    from main import build_agent

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
        # Honest per-task test verdict, kept SEPARATE from run_status. Values:
        # "none" | "done" | "failed" | "inconclusive" | "skipped". A timed-out /
        # exit_code-less run is "inconclusive", never silently "done".
        "result_status": "none",
        "repair_proposed": False,
        "inbox_pending_after": 0,
        "error": None,
    }

    # Dry-run visibility (observability only — never changes behaviour). Read
    # the PREVIOUS heartbeat before overwriting it so the streak is accurate.
    # Extraction lives here (impure I/O); the helper itself stays pure.
    _prev_hb = _read_heartbeat(workspace)
    try:
        _prev_streak = max(0, int((_prev_hb or {}).get("dry_run_streak", 0) or 0))
    except (TypeError, ValueError):
        _prev_streak = 0
    # At tick_start no work has happened yet, so carry the streak forward
    # (did_work=False). The final tick_complete recomputes it once we know
    # whether this tick actually ran a dry-run pass. An idle no-op or an early
    # crash therefore never inflates the dry-run-stall signal.
    visibility = _dry_run_visibility(
        dry_run=dry_run,
        previous_streak=_prev_streak,
        processed_effects=0,  # no effect is ever applied in this layer
        did_work=False,
    )
    summary.update(visibility)

    # Heartbeat #1: record that a tick STARTED before doing any work. Even if
    # the tick later crashes, the liveness file proves the daemon is running.
    _write_heartbeat(
        workspace,
        {"event": "tick_start", "tick_start": tick_start, "dry_run": dry_run,
         **visibility},
    )

    try:
        # ── 1. Scheduler tick — enqueue due schedules ─────────────────────────
        sched_store = SchedulerStore(workspace / SCHEDULES_PATH)
        task_store  = TaskQueueStore(workspace / TASK_QUEUE_PATH)
        tick_report = sched_store.tick(task_queue=task_store)
        summary["schedules_due"]   = tick_report.due_count
        summary["tasks_enqueued"]  = tick_report.enqueued_count
        _log_tick(workspace, {"event": "scheduler_tick", **tick_report.to_dict()})

        # ── 2. Claim and run pending tasks ────────────────────────────────────
        pending_tasks = task_store.list(status="pending")
        if not pending_tasks:
            _log_tick(workspace, {"event": "no_pending_tasks"})
            # Still check approval inbox below
        else:
            agent = build_agent(workspace, with_memory=False, approval_provider=None)
            inbox = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
            runtime = AutonomousRuntime(agent, workspace=workspace, approval_inbox=inbox)

            for task in pending_tasks:
                try:
                    task_store.mark_running(task.id)
                except Exception:
                    continue   # another process already claimed it

                config = _config_from_task(task)
                # Always honour dry_run flag from CLI / env
                import dataclasses
                if dry_run and not config.dry_run:
                    config = dataclasses.replace(config, dry_run=True)

                run_report = runtime.run(config)
                summary["tasks_processed"] += 1

                # Per-task honest verdict (kept distinct from run_status below).
                task_result_status = "none"

                # Record test outcomes
                for task_report in run_report.tasks:
                    if task_report.task.kind == "tests":
                        summary["tests_result"] = {
                            "status": task_report.status,
                            "summary": task_report.summary,
                            **task_report.details,
                        }
                        summary["tests_health"] = _classify_test_health(
                            summary["tests_result"]
                        )
                        # The runtime already classified done/failed/inconclusive;
                        # propagate it verbatim as the result_status of this task.
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
                            # Do NOT propose a repair (no specific failing test),
                            # but never let this pass silently as healthy.
                            _log_tick(
                                workspace,
                                {
                                    "event": "tests_inconclusive",
                                    "reason": task_report.summary,
                                    "details": task_report.details,
                                },
                            )

                task_store.mark_done(task.id, report=run_report.to_dict())
                # run_status = did the run finish (completed); result_status =
                # what the work actually established (done/failed/inconclusive).
                # mode/processed_effects make it explicit that a dry-run task
                # applied nothing.
                _log_tick(workspace, {"event": "task_done", "task_id": task.id,
                                      "run_status": run_report.status,
                                      "result_status": task_result_status,
                                      "mode": summary["mode"],
                                      "effects": summary["effects"],
                                      "processed_effects": summary["processed_effects"]})

        # ── 3. Tally inbox ────────────────────────────────────────────────────
        inbox_check = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
        summary["inbox_pending_after"] = len(inbox_check.pending())

    except Exception as exc:  # noqa: BLE001
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
    # Recompute the streak now that we know whether this tick did real work.
    # mode/effects/processed_effects are unchanged by did_work; only the streak
    # differs: a working dry-run pass increments, an idle no-op carries forward.
    visibility = _dry_run_visibility(
        dry_run=dry_run,
        previous_streak=_prev_streak,
        processed_effects=0,
        did_work=summary["tasks_processed"] > 0,
    )
    summary.update(visibility)
    _log_tick(workspace, {"event": "tick_complete", **summary})

    # Heartbeat #2: record successful completion with the honest health verdict.
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
            **visibility,
        },
    )

    # Human-readable summary to stderr (captured by Task Scheduler logs)
    pending = summary["inbox_pending_after"]
    tests   = summary["tests_result"]
    health  = summary["tests_health"]
    # Honest, machine-greppable visibility line: never implies effects ran.
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
    if summary["repair_proposed"]:
        print("[agent_tick] Repair proposal added to inbox.", file=sys.stderr)
    if pending:
        print(f"[agent_tick] {pending} item(s) waiting in approval inbox.", file=sys.stderr)
    else:
        print("[agent_tick] Inbox clear.", file=sys.stderr)

    return 0


# ── paced campaign daemon mode ────────────────────────────────────────────────

def run_paced_campaign(
    workspace: Path,
    *,
    dry_run: bool = True,
    goal: str = "project health",
    max_cycles: int = 24,
    cycle_pause_seconds: int = 0,
    max_wall_clock_seconds: int = 0,
    max_llm_calls: int = 100,
    max_cost_units: int = 0,
    heartbeat_fn: Callable[[Path, dict], None] | None = None,
    run_campaign_fn: Callable[..., Any] | None = None,
    build_agent_fn: Callable[[Path], Any] | None = None,
) -> int:
    """Run ONE long, PACED autonomous campaign as a single daemon process.

    Unlike :func:`run_tick` (a single bounded pass driven externally by Task
    Scheduler), this drives the multi-cycle campaign loop itself over real
    wall-clock time: it sleeps ``cycle_pause_seconds`` between cycles and stops
    at ``max_wall_clock_seconds``. Crucially it emits a heartbeat PER CYCLE so
    the operator's ``--status`` view shows liveness throughout a multi-hour
    unattended run instead of going dark between the start and the end.

    Dry-run by default; effects still flow only through the existing approval
    gate inside the campaign. The heavy collaborators (heartbeat writer, the
    campaign loop, agent builder) are injectable so the wiring is testable with
    deterministic fakes. Returns an exit code (0 = ok, 1 = hard error).
    """
    from core.campaign import (
        CampaignConfig,
        CampaignLedger,
        run_campaign as _real_run_campaign,
    )

    write_heartbeat = heartbeat_fn or _write_heartbeat
    run_campaign = run_campaign_fn or _real_run_campaign

    def _on_cycle(snapshot: dict) -> None:
        # Liveness during a long paced run. A heartbeat-write failure (e.g. a
        # transient disk error) must NEVER kill a multi-hour campaign, so the
        # per-cycle write is best-effort at this daemon layer (the core seam
        # in run_campaign stays pure).
        payload = {
            "event": "campaign_cycle",
            "dry_run": dry_run,
            "mode": "dry_run" if dry_run else "live",
            "effects": "disabled" if dry_run else "enabled",
            **snapshot,
        }
        try:
            write_heartbeat(workspace, payload)
        except Exception:
            pass

    try:
        config = CampaignConfig(
            goal=goal,
            dry_run=dry_run,
            max_cycles=max_cycles,
            max_llm_calls=max_llm_calls,
            max_cost_units=max_cost_units,
            cycle_pause_seconds=cycle_pause_seconds,
            max_wall_clock_seconds=max_wall_clock_seconds,
        )
    except ValueError as exc:
        print(f"[agent_tick] campaign config error: {exc}", file=sys.stderr)
        return 1

    # Heartbeat #1: prove the campaign process started before any cycle runs.
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
        from main import build_agent
        agent = build_agent(workspace, with_memory=False, approval_provider=None)

    from core.approval_inbox import ApprovalInbox
    inbox = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
    ledger = CampaignLedger(path=workspace / DATA_DIR / "campaign_ledger.jsonl")

    print(
        f"[agent_tick] paced campaign goal={config.goal!r} dry_run={dry_run} "
        f"max_cycles={config.max_cycles} pause={config.cycle_pause_seconds}s "
        f"ceiling={config.max_wall_clock_seconds}s",
        file=sys.stderr,
    )

    try:
        result = run_campaign(
            config,
            agent=agent,
            workspace=workspace,
            approval_inbox=inbox,
            ledger=ledger,
            on_cycle=_on_cycle,
        )
    except Exception as exc:
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

    # Final heartbeat + honest tick-log summary.
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
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    ws   = Path(args.workspace).resolve()

    if args.status:
        sys.exit(_print_status(ws))

    dry = not args.allow_effects
    # Respect env var override too
    if os.environ.get("AGENT_TICK_DRY_RUN", "1").strip().lower() in {"0", "false", "no"}:
        dry = False

    if args.campaign:
        sys.exit(run_paced_campaign(
            ws,
            dry_run=dry,
            goal=args.goal,
            max_cycles=args.max_cycles,
            cycle_pause_seconds=args.cycle_pause_seconds,
            max_wall_clock_seconds=args.max_wall_clock_seconds,
            max_llm_calls=args.max_llm_calls,
            max_cost_units=args.max_cost_units,
        ))

    sys.exit(run_tick(ws, dry_run=dry))
