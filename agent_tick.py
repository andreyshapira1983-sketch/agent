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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_tick(workspace: Path, payload: dict) -> None:
    log_path = workspace / LOGS_DIR / TICK_LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _now_iso(), **payload}, ensure_ascii=False) + "\n")


# ── status-only mode ──────────────────────────────────────────────────────────

def _print_status(workspace: Path) -> int:
    """Print pending inbox items and exit. No agent is created."""
    from core.approval_inbox import ApprovalInbox
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
        "repair_proposed": False,
        "inbox_pending_after": 0,
        "error": None,
    }

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

                # Record test outcomes
                for task_report in run_report.tasks:
                    if task_report.task.kind == "tests":
                        summary["tests_result"] = {
                            "status": task_report.status,
                            "summary": task_report.summary,
                            **task_report.details,
                        }
                        if task_report.status == "failed":
                            repair_result = _maybe_propose_repair(
                                workspace, task_report.details, inbox, agent
                            )
                            summary["repair_proposed"] = repair_result.get(
                                "repair_proposed", False
                            )
                            _log_tick(workspace, {"event": "repair_attempt", **repair_result})

                task_store.mark_done(task.id, report=run_report.to_dict())
                _log_tick(workspace, {"event": "task_done", "task_id": task.id,
                                      "run_status": run_report.status})

        # ── 3. Tally inbox ────────────────────────────────────────────────────
        inbox_check = ApprovalInbox(path=workspace / APPROVAL_INBOX_PATH)
        summary["inbox_pending_after"] = len(inbox_check.pending())

    except Exception as exc:  # noqa: BLE001
        summary["error"] = f"{type(exc).__name__}: {exc}"
        _log_tick(workspace, {"event": "tick_error", "error": summary["error"],
                              "traceback": traceback.format_exc()})
        print(f"[agent_tick] ERROR: {exc}", file=sys.stderr)
        return 1

    summary["tick_end"] = _now_iso()
    _log_tick(workspace, {"event": "tick_complete", **summary})

    # Human-readable summary to stderr (captured by Task Scheduler logs)
    pending = summary["inbox_pending_after"]
    tests   = summary["tests_result"]
    if tests:
        print(
            f"[agent_tick] tests={tests.get('status','?')} {tests.get('summary','')}",
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
