"""First live-effect probe — DETERMINISTIC, no LLM, single-file artifact.

Purpose
-------
This is the agent's first *controlled live effect*. Its job is NOT to prove the
agent is smart — it is to prove the live-effect contour is SAFE: the system can
create exactly one artifact from already-computed signals, log it honestly, and
nothing else moves.

What it does
------------
1. Gathers read-only signals that other layers already compute:
   - timestamp (UTC)
   - daemon status (missing / stale / live) + heartbeat age
   - dry_run_streak
   - result_status / tests_health of the last tick
   - best-next-action (deterministic priority pick)
   - approval inbox triage summary
2. Writes ONE new file: ``reports/first_live_probe.md``.

Hard boundaries (enforced by this script)
-----------------------------------------
- Refuses to overwrite an existing report (no clobber) — use --force only after
  an explicit operator decision.
- Touches no code, no agent memory, no approval inbox, runs no repair, makes no
  network calls. It only READS runtime state and WRITES the single report file.

Rollback
--------
    Remove-Item reports/first_live_probe.md

Usage
-----
    python scripts/first_live_probe.py            # write the report (refuse if exists)
    python scripts/first_live_probe.py --print     # preview content, write nothing
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the workspace root importable when run as a script.
WORKSPACE = Path(__file__).resolve().parent.parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import agent_tick  # noqa: E402
from core.alert_ack import AlertAckStore  # noqa: E402
from core.approval_inbox import ApprovalInbox  # noqa: E402
from core.approval_triage import triage_inbox  # noqa: E402
from core.best_next_action import select_best_next_action  # noqa: E402

REPORT_PATH = Path("reports") / "first_live_probe.md"


def _gather_signals(workspace: Path) -> dict:
    """Collect every signal READ-ONLY. No mutation, no execution, no network."""
    heartbeat = agent_tick._read_heartbeat(workspace)
    age = agent_tick._heartbeat_age_seconds(heartbeat)
    hb = heartbeat or {}

    inbox = ApprovalInbox(path=workspace / "data" / "approval_inbox.jsonl")
    triage = triage_inbox(inbox.pending())

    ack_store = AlertAckStore(path=workspace / "data" / "alert_acknowledgements.jsonl")
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

    if heartbeat is None:
        daemon_status = "missing"
    elif agent_tick._is_stale(age):
        daemon_status = "stale"
    else:
        daemon_status = "live"

    return {
        "heartbeat": hb,
        "age": age,
        "daemon_status": daemon_status,
        "triage": triage,
        "action": action,
        "acknowledged": sorted(acknowledged),
    }


def _format_age(age: float | None) -> str:
    if age is None:
        return "unknown (no heartbeat timestamp)"
    if age < 90:
        return f"{age:.0f}s"
    if age < 5400:
        return f"{age / 60:.1f} min"
    return f"{age / 3600:.1f} h"


def _render(signals: dict) -> str:
    hb = signals["heartbeat"]
    triage = signals["triage"]
    action = signals["action"]
    now = datetime.now(timezone.utc).isoformat()

    lines: list[str] = []
    lines.append("# First live probe")
    lines.append("")
    lines.append(
        "Deterministic diagnostic snapshot — generated without the LLM agent "
        "loop. This is the first controlled live effect: one artifact, "
        "already-computed signals, no code/memory/approval/repair touched."
    )
    lines.append("")
    lines.append(f"- **Generated (UTC):** {now}")
    lines.append(f"- **Daemon status:** {signals['daemon_status']}")
    lines.append(f"- **Heartbeat age:** {_format_age(signals['age'])}")
    lines.append(f"- **Heartbeat mode:** {hb.get('mode', 'unknown')}")
    lines.append(f"- **dry_run_streak:** {hb.get('dry_run_streak', 0)}")
    lines.append(f"- **Last tick result_status:** {hb.get('result_status', 'none')}")
    lines.append(f"- **Last tick tests_health:** {hb.get('tests_health', 'none')}")
    lines.append(f"- **Last tick event:** {hb.get('event', 'none')}")
    if hb.get("error"):
        lines.append(f"- **Last tick error:** {hb.get('error')}")
    lines.append("")

    lines.append("## Best next action (deterministic priority pick)")
    lines.append("")
    lines.append(f"- **Action:** {action.action}")
    lines.append(f"- **Title:** {action.title}")
    lines.append(f"- **Severity:** {action.severity} (priority {action.priority})")
    lines.append(f"- **Risk:** {action.risk}")
    lines.append(f"- **Reason:** {action.reason}")
    if action.recommended_command:
        lines.append(f"- **Recommended command:** `{action.recommended_command}`")
    if action.evidence:
        lines.append("- **Evidence:**")
        for ev in action.evidence:
            lines.append(f"  - {ev}")
    if action.unknowns:
        lines.append("- **Honest unknowns:**")
        for unk in action.unknowns:
            lines.append(f"  - {unk}")
    if signals["acknowledged"]:
        lines.append(
            f"- **Suppressed (operator-acknowledged):** {', '.join(signals['acknowledged'])}"
        )
    lines.append("")

    lines.append("## Approval inbox triage summary")
    lines.append("")
    lines.append(f"- **Total pending:** {triage.total_pending}")
    lines.append(f"- **Clusters:** {len(triage.clusters)}")
    lines.append(f"- **Duplicates (dismissable):** {len(triage.duplicates)}")
    lines.append(f"- **Stale (>72h):** {len(triage.stale)}")
    lines.append(f"- **Dangerous (irreversible/external):** {len(triage.dangerous)}")
    lines.append(f"- **Low-value (no rationale):** {len(triage.low_value)}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_Rollback: delete this file (`Remove-Item reports/first_live_probe.md`). "
        "Regenerate: `python scripts/first_live_probe.py`._"
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic first live-effect probe.")
    parser.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="Preview the report content on stdout; write no file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing report (requires explicit operator intent).",
    )
    args = parser.parse_args(argv)

    signals = _gather_signals(WORKSPACE)
    content = _render(signals)

    if args.print_only:
        sys.stdout.write(content)
        return 0

    target = WORKSPACE / REPORT_PATH
    if target.exists() and not args.force:
        print(
            f"(refused: {REPORT_PATH.as_posix()} already exists — "
            "delete it or pass --force; this probe never clobbers silently)",
            file=sys.stderr,
        )
        return 2

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(f"wrote {REPORT_PATH.as_posix()} ({len(content.encode('utf-8'))} bytes)")
    print("rollback: Remove-Item reports/first_live_probe.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
