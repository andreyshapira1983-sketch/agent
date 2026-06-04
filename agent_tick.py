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

    try:
        from core.repair_proposal import RepairProposalGenerator

        llm = getattr(agent, "llm", None)
        if llm is None:
            return {"repair_proposed": False, "reason": "no llm on agent"}

        gen = RepairProposalGenerator(llm=llm, workspace=workspace)
        report = gen.generate(target_path=None)   # let generator pick based on logs

        if report.status == "proposed" and report.proposal is not None:
            prop = report.proposal
            summary_lines = [
                f"{failed} test(s) failed: {', '.join(failed_names[:5])}",
                f"Proposed fix → {prop.target_file}: {prop.description}",
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
                    "target_file": prop.target_file,
                    "patch_preview": prop.patch_preview[:500] if prop.patch_preview else "",
                    "confidence": report.confidence,
                    "diagnosis": report.diagnosis,
                },
            )
            return {"repair_proposed": True, "target": prop.target_file}

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

    # Heartbeat #1: record that a tick STARTED before doing any work. Even if
    # the tick later crashes, the liveness file proves the daemon is running.
    _write_heartbeat(
        workspace,
        {"event": "tick_start", "tick_start": tick_start, "dry_run": dry_run},
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
                _log_tick(workspace, {"event": "task_done", "task_id": task.id,
                                      "run_status": run_report.status,
                                      "result_status": task_result_status})

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
            },
        )
        print(f"[agent_tick] ERROR: {exc}", file=sys.stderr)
        return 1

    summary["tick_end"] = _now_iso()
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
        },
    )

    # Human-readable summary to stderr (captured by Task Scheduler logs)
    pending = summary["inbox_pending_after"]
    tests   = summary["tests_result"]
    health  = summary["tests_health"]
    mode    = "DRY-RUN (no effects applied)" if dry_run else "LIVE (effects allowed)"
    print(f"[agent_tick] mode={mode}", file=sys.stderr)
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

    sys.exit(run_tick(ws, dry_run=dry))
