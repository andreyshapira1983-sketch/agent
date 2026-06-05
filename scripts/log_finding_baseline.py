"""Deterministic log-finding baseline — read ONE log, find ONE problem, NO LLM.

Purpose
-------
This is the *baseline* for the agent's first useful task ("check one log → find
one problem → one report"). It applies HARD, fixed rules to
``logs/daemon_tick.jsonl`` and emits the single most important CONFIRMED problem
as an "expected finding". It is the reference the LLM agent run is compared
against — does the agent match this finding, add useful intelligence on top, or
hallucinate something the rules never saw?

It writes NOTHING by default (prints the finding). It never calls the LLM, never
touches code/memory/inbox, makes no network calls.

Hard rules (priority order — the FIRST that matches wins)
---------------------------------------------------------
R1 (severity=high)  a ``repair_attempt`` record with ``repair_proposed=false``
                    and a ``reason`` beginning ``exception:`` → the auto-repair
                    SAFETY NET threw. This is the worst class: the one mechanism
                    that fires exactly when tests break is itself broken.
R2 (severity=high)  any record with a non-null ``error`` field.
R3 (severity=medium) a ``tick_complete`` with ``result_status='failed'`` or
                    ``tests_health='fail'`` → a real test failure was recorded.

If nothing matches, the finding is "no confirmed problem" (severity=none).

Usage
-----
    python scripts/log_finding_baseline.py            # print the finding
    python scripts/log_finding_baseline.py --json      # machine-readable finding
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
LOG_PATH = Path("logs") / "daemon_tick.jsonl"


def _load_records(path: Path) -> list[dict]:
    """Read a plain JSONL log. Malformed lines are skipped (recorded separately
    by the caller via the returned parse-failure count is out of scope here)."""
    records: list[dict] = []
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def find_problem(records: list[dict]) -> dict:
    """Apply the hard rules in priority order; return ONE finding dict."""
    # R1 — broken auto-repair safety net.
    for idx, rec in enumerate(records, 1):
        if rec.get("event") != "repair_attempt":
            continue
        if rec.get("repair_proposed"):
            continue
        reason = str(rec.get("reason", ""))
        if reason.startswith("exception:"):
            return {
                "rule": "R1",
                "severity": "high",
                "title": "Daemon auto-repair safety net is broken (never proposes a fix)",
                "log_line": idx,
                "log_ts": rec.get("ts"),
                "evidence": reason,
                "impact": (
                    "When a tick records failing tests the daemon is supposed to "
                    "generate a RepairProposal and queue it for approval; instead "
                    "it raises and proposes nothing. The single mechanism that "
                    "fires exactly when something breaks is itself broken."
                ),
            }

    # R2 — any explicit error.
    for idx, rec in enumerate(records, 1):
        err = rec.get("error")
        if err:
            return {
                "rule": "R2",
                "severity": "high",
                "title": f"Tick recorded an error ({rec.get('event', '?')})",
                "log_line": idx,
                "log_ts": rec.get("ts"),
                "evidence": str(err),
                "impact": "A tick logged a non-null error field.",
            }

    # R3 — a real test failure was recorded.
    for idx, rec in enumerate(records, 1):
        if rec.get("event") != "tick_complete":
            continue
        if rec.get("result_status") == "failed" or rec.get("tests_health") == "fail":
            return {
                "rule": "R3",
                "severity": "medium",
                "title": "A tick recorded a real test failure",
                "log_line": idx,
                "log_ts": rec.get("ts"),
                "evidence": json.dumps(rec.get("tests_result", {}), ensure_ascii=False),
                "impact": "Tests failed during a daemon tick.",
            }

    return {
        "rule": "none",
        "severity": "none",
        "title": "No confirmed problem found by the hard rules",
        "log_line": None,
        "log_ts": None,
        "evidence": "",
        "impact": "",
    }


def _summarise(records: list[dict]) -> dict:
    import collections

    events = collections.Counter(r.get("event", "?") for r in records)
    return {
        "total_records": len(records),
        "event_counts": dict(events),
    }


def build_finding() -> dict:
    records = _load_records(WORKSPACE / LOG_PATH)
    return {
        "log": LOG_PATH.as_posix(),
        "summary": _summarise(records),
        "finding": find_problem(records),
    }


def _render(result: dict) -> str:
    f = result["finding"]
    s = result["summary"]
    lines: list[str] = []
    lines.append("=== DETERMINISTIC LOG-FINDING BASELINE (no LLM) ===")
    lines.append(f"log: {result['log']}")
    lines.append(
        f"records: {s['total_records']}  events: "
        + ", ".join(f"{k}={v}" for k, v in sorted(s["event_counts"].items()))
    )
    lines.append("")
    lines.append(f"FINDING (rule {f['rule']}, severity={f['severity']}):")
    lines.append(f"  {f['title']}")
    if f["log_line"] is not None:
        lines.append(f"  log line: {f['log_line']}  ts: {f['log_ts']}")
    if f["evidence"]:
        lines.append(f"  evidence: {f['evidence']}")
    if f["impact"]:
        lines.append(f"  impact: {f['impact']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic daemon-log problem finder (no LLM).")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    result = build_finding()
    if args.json:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(_render(result))
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
