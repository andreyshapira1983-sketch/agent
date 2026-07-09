"""Read-only local health panel commands.

``:dry-health-pass`` is intentionally a collector, not a runner: it never
starts the planner, the self-build producer, self-apply, tests, approvals, or
network work. It reads small local state files best-effort and turns failures
into ``unknown`` fields.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from cli.parsers import _split_meta_args
from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH as APPROVAL_INBOX_PATH
from core.backlog_target_mapper import map_backlog_candidate
from core.budget_ledger import BudgetLedger, DEFAULT_COUNTERS
from core.budget_kill_switch import BudgetKillSwitch, default_path as kill_switch_path
from core.self_build_producer import DEFAULT_CANDIDATE_TARGETS
from core.state_integrity import StateIntegrityError, decode_state_row
from core.value_review import VALID_VERDICTS


RUNTIME_TASKS_PATH = Path("data") / "runtime_tasks.jsonl"
RUNTIME_SCHEDULES_PATH = Path("data") / "runtime_schedules.jsonl"
BUDGET_LEDGER_PATH = Path("data") / "budget_ledger.jsonl"
BUDGET_CONFIG_PATH = Path("config") / "budget_limits.json"
SELF_BUILD_STATE_PATH = Path("data") / "self_build_producer_state.json"
VALUE_REVIEWS_PATH = Path("data") / "value_reviews.jsonl"

VALUE_VERDICTS = (
    "accepted",
    "rejected_low_value",
    "rejected_misleading_summary",
    "rejected_risky",
    "rejected_wrong_target",
)

NEXT_KILL_SWITCH = "inspect kill-switch reason"
NEXT_GIT_DIRTY = "inspect working tree before running autonomous work"
NEXT_APPROVALS = "review approval inbox"
NEXT_DAEMON = "keep manual mode and verify scheduler before background run"
NEXT_DAEMON_STALE_MANUAL = (
    "daemon heartbeat is stale but expected in manual mode; "
    "run python agent_tick.py only if you want a fresh scheduled-daemon signal"
)
NEXT_GROUNDED = "implement grounded target mapper / allowlist follow-up"
NEXT_PAUSED = "inspect paused tasks before resuming"
NEXT_DRY_RUN = "run one manual dry-run tick and inspect the health panel again"

GitStatusFn = Callable[[Path], dict[str, Any]]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _count_value(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_json_file(path: Path) -> tuple[dict[str, Any] | None, str]:
    if not path.exists():
        return None, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None, "broken"
    if not isinstance(data, dict):
        return None, "broken"
    return data, "known"


def _read_jsonl_payloads(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Read a JSONL state file without repair, quarantine, upgrade, or writes."""
    if not path.exists():
        return [], "missing"
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return [], "broken"

    rows: list[dict[str, Any]] = []
    for raw_line in raw_lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            payload = decode_state_row(stripped)
        except (StateIntegrityError, ValueError, TypeError):
            return [], "broken"
        if not isinstance(payload, dict):
            return [], "broken"
        rows.append(payload)
    return rows, "known"


def _count_status(path: Path, status: str) -> dict[str, Any]:
    rows, state = _read_jsonl_payloads(path)
    if state != "known":
        return {"status": "unknown", "count": None, "reason": state, "path": str(path)}
    return {
        "status": "known",
        "count": sum(1 for row in rows if row.get("status") == status),
        "path": str(path),
    }


def _daemon_payload(workspace: Path, now: datetime) -> dict[str, Any]:
    try:
        import agent_tick

        threshold = (
            agent_tick.EXPECTED_TICK_INTERVAL_SECONDS * agent_tick.STALENESS_FACTOR
        )
        heartbeat = agent_tick._read_heartbeat(workspace)
        age = agent_tick._heartbeat_age_seconds(heartbeat, now=now)
        if heartbeat is None or age is None:
            status = "unknown"
        elif agent_tick._is_stale(age):
            status = "stale"
        else:
            status = "alive"
        last_tick_at = None
        if isinstance(heartbeat, dict) and heartbeat.get("ts"):
            last_tick_at = str(heartbeat.get("ts"))
        return {
            "status": status,
            "age_seconds": age,
            "event": heartbeat.get("event") if isinstance(heartbeat, dict) else None,
            "last_tick_at": last_tick_at,
            "staleness_threshold_seconds": threshold,
            "path": str(workspace / agent_tick.HEARTBEAT_PATH),
        }
    except Exception as exc:  # noqa: BLE001 - status must not crash
        return {
            "status": "unknown",
            "age_seconds": None,
            "event": None,
            "last_tick_at": None,
            "staleness_threshold_seconds": None,
            "reason": type(exc).__name__,
        }


def _daemon_manual_mode_expected(scheduler: dict[str, Any]) -> bool:
    """No due scheduled work — Stage-0 manual runs do not refresh heartbeat."""
    if scheduler.get("status") != "known":
        return False
    due = scheduler.get("due")
    return isinstance(due, int) and due == 0


def _daemon_interpretation(
    daemon_status: str,
    *,
    scheduler: dict[str, Any],
) -> str:
    if daemon_status == "alive":
        return "scheduled_daemon_recent_tick"
    if daemon_status == "unknown":
        return "no_heartbeat"
    if daemon_status == "stale" and _daemon_manual_mode_expected(scheduler):
        return "expected_without_scheduled_daemon"
    if daemon_status == "stale":
        return "stale_scheduled_daemon_expected"
    return "unknown"


def _enrich_daemon_payload(
    daemon: dict[str, Any], scheduler: dict[str, Any]
) -> dict[str, Any]:
    out = dict(daemon)
    status = str(daemon.get("status") or "unknown")
    out["interpretation"] = _daemon_interpretation(status, scheduler=scheduler)
    return out


def _daemon_blocks_next_action(daemon: dict[str, Any]) -> bool:
    status = daemon.get("status")
    if status == "unknown":
        return True
    if status == "stale":
        return daemon.get("interpretation") != "expected_without_scheduled_daemon"
    return False


def _scheduler_due(workspace: Path, now: datetime) -> dict[str, Any]:
    path = workspace / RUNTIME_SCHEDULES_PATH
    rows, state = _read_jsonl_payloads(path)
    if state != "known":
        return {"status": "unknown", "due": None, "reason": state, "path": str(path)}
    due = 0
    for row in rows:
        if row.get("status") != "active":
            continue
        next_run = _parse_iso(row.get("next_run_at"))
        if next_run is None:
            return {"status": "unknown", "due": None, "reason": "broken", "path": str(path)}
        if next_run <= now:
            due += 1
    return {"status": "known", "due": due, "path": str(path)}


def _budget_payload(workspace: Path, now: datetime) -> dict[str, Any]:
    try:
        ledger = BudgetLedger.from_env(
            path=workspace / BUDGET_LEDGER_PATH,
            config_path=workspace / BUDGET_CONFIG_PATH,
        )
    except Exception as exc:  # noqa: BLE001 - invalid config/env is advisory
        return {"status": "unknown", "reason": type(exc).__name__, "snapshot": None}

    rows, state = _read_jsonl_payloads(workspace / BUDGET_LEDGER_PATH)
    if state != "known":
        windows = []
        for window in ledger.windows:
            windows.append(
                {
                    "name": window.name,
                    "seconds": window.seconds,
                    "status": "unknown",
                    "counters": {
                        counter: {"used": None, "limit": window.limit_for(counter)}
                        for counter in sorted(set(DEFAULT_COUNTERS) | set(window.limits))
                    },
                }
            )
        return {
            "status": "unknown",
            "reason": state,
            "path": str(workspace / BUDGET_LEDGER_PATH),
            "config_path": str(workspace / BUDGET_CONFIG_PATH),
            "windows": windows,
            "snapshot": None,
        }

    records: list[dict[str, Any]] = []
    for row in rows:
        counter = str(row.get("counter") or "")
        amount = row.get("amount")
        created = _parse_iso(row.get("created_at"))
        if not counter or created is None:
            return {"status": "unknown", "reason": "broken", "snapshot": None}
        try:
            amount_int = max(1, int(amount))
        except (TypeError, ValueError):
            return {"status": "unknown", "reason": "broken", "snapshot": None}
        records.append({"counter": counter, "amount": amount_int, "created_at": created})

    windows = []
    for window in ledger.windows:
        counters = {}
        cutoff = now - timedelta(seconds=window.seconds)
        for counter in sorted(set(DEFAULT_COUNTERS) | set(window.limits)):
            used = sum(
                int(record["amount"])
                for record in records
                if record["counter"] == counter and record["created_at"] >= cutoff
            )
            counters[counter] = {"used": used, "limit": window.limit_for(counter)}
        windows.append(
            {
                "name": window.name,
                "seconds": window.seconds,
                "status": "known",
                "counters": counters,
            }
        )
    totals: dict[str, int] = {}
    for record in records:
        totals[record["counter"]] = totals.get(record["counter"], 0) + int(record["amount"])
    snapshot = {
        "path": str(workspace / BUDGET_LEDGER_PATH),
        "config_path": str(workspace / BUDGET_CONFIG_PATH),
        "windows": windows,
        "totals": totals,
    }
    return {
        "status": "known",
        "path": str(workspace / BUDGET_LEDGER_PATH),
        "config_path": str(workspace / BUDGET_CONFIG_PATH),
        "windows": windows,
        "snapshot": snapshot,
    }


def _window_by_name(budget: dict[str, Any], name: str) -> dict[str, Any]:
    for window in budget.get("windows") or []:
        if isinstance(window, dict) and window.get("name") == name:
            return window
    return {"name": name, "status": "unknown", "counters": {}}


def _kill_switch_payload(workspace: Path, budget: dict[str, Any]) -> dict[str, Any]:
    snapshot = budget.get("snapshot") if isinstance(budget, dict) else None
    path = kill_switch_path(workspace)
    try:
        if snapshot is None and not path.exists():
            return {"status": "unknown", "active": None, "reason": "missing", "path": str(path)}
        state = BudgetKillSwitch(path=path).status(snapshot)
        data = state.to_dict()
        return {
            "status": "active" if data.get("active") else "inactive",
            "active": bool(data.get("active")),
            "reason": data.get("reason") or "",
            "path": str(path),
            **data,
        }
    except Exception as exc:  # noqa: BLE001 - health panel is best-effort
        return {"status": "unknown", "active": None, "reason": type(exc).__name__, "path": str(path)}


def _cooldown_payload(workspace: Path, now: datetime) -> dict[str, Any]:
    state, state_status = _read_json_file(workspace / SELF_BUILD_STATE_PATH)
    if state_status != "known":
        return {
            "status": "unknown",
            "remaining_seconds": None,
            "last_proposed_at": None,
            "reason": state_status,
            "path": str(workspace / SELF_BUILD_STATE_PATH),
        }
    try:
        import agent_tick

        hours = agent_tick._self_build_cooldown_hours()
        remaining = agent_tick._cooldown_remaining_seconds(
            state or {}, cooldown_hours=hours, now=now
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "unknown",
            "remaining_seconds": None,
            "last_proposed_at": state.get("last_proposed_at") if state else None,
            "reason": type(exc).__name__,
            "path": str(workspace / SELF_BUILD_STATE_PATH),
        }
    return {
        "status": "ready" if remaining <= 0 else "cooldown",
        "remaining_seconds": int(remaining),
        "cooldown_hours": hours,
        "last_proposed_at": state.get("last_proposed_at") if state else None,
        "path": str(workspace / SELF_BUILD_STATE_PATH),
    }


def _read_text_or_status(path: Path) -> tuple[str, str]:
    if not path.exists():
        return "", "missing"
    try:
        return path.read_text(encoding="utf-8"), "known"
    except (OSError, UnicodeDecodeError):
        return "", "broken"


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return True


def _grounded_target_payload(workspace: Path) -> dict[str, Any]:
    tech_debt, td_status = _read_text_or_status(workspace / "TECH_DEBT.md")
    anatomy, anatomy_status = _read_text_or_status(workspace / "docs" / "AGENT_ANATOMY.md")
    proposal, proposal_status = _read_text_or_status(
        workspace / "docs" / "proposals" / "self-build-grounded-target-coverage-proposal.md"
    )
    if td_status != "known" and anatomy_status != "known" and proposal_status != "known":
        return {"classification": "unknown", "reason": "missing", "target_path": None}
    if td_status == "broken" or anatomy_status == "broken" or proposal_status == "broken":
        return {"classification": "unknown", "reason": "broken", "target_path": None}
    try:
        from core.backlog_selector import build_backlog, select_top
        from core.self_build_producer import _is_critical

        candidate = select_top(
            build_backlog(
                tech_debt_text=tech_debt,
                anatomy_text=anatomy,
                self_build_proposal_text=proposal,
                include_self_build_docs=not _path_exists(
                    workspace / "docs" / "self_build.md"
                ),
            )
        )
        if candidate is None:
            return {"classification": "none", "target_path": None, "evidence_ref": None}
        mapping = map_backlog_candidate(
            candidate,
            workspace=workspace,
            allowed_targets=DEFAULT_CANDIDATE_TARGETS,
        )
        if mapping.ok and mapping.candidate is not None:
            target = mapping.candidate.target_path
            classification = "mapped" if mapping.decision == "mapped" else "available"
        elif mapping.decision == "unknown":
            return {
                "classification": "unknown",
                "reason": mapping.reason,
                "target_path": mapping.target_path,
                "evidence_ref": candidate.evidence_ref,
                "signal_source": candidate.signal_source,
                "problem_quote": candidate.problem_quote,
                "mapping_decision": mapping.decision,
                "mapping_rule": mapping.mapping_rule,
            }
        else:
            target = candidate.target_path
            classification = "off_allowlist"
        if _is_critical(target):
            classification = "off_allowlist"
        return {
            "classification": classification,
            "target_path": target,
            "evidence_ref": mapping.evidence_ref or candidate.evidence_ref,
            "signal_source": candidate.signal_source,
            "problem_quote": candidate.problem_quote,
            "source_target_path": candidate.target_path,
            "mapping_decision": mapping.decision,
            "mapping_rule": mapping.mapping_rule,
            "mapping_reason": mapping.reason,
        }
    except Exception as exc:  # noqa: BLE001
        return {"classification": "unknown", "reason": type(exc).__name__, "target_path": None}


def _self_build_payload(workspace: Path, now: datetime) -> dict[str, Any]:
    return {
        "cooldown": _cooldown_payload(workspace, now),
        "grounded_target": _grounded_target_payload(workspace),
    }


def _value_review_payload(workspace: Path) -> dict[str, Any]:
    path = workspace / VALUE_REVIEWS_PATH
    rows, state = _read_jsonl_payloads(path)
    counts = {verdict: 0 for verdict in VALUE_VERDICTS}
    if state != "known":
        return {"status": "unknown", "reason": state, "counts": counts, "path": str(path)}
    effective: dict[str, str] = {}
    for row in rows:
        item_id = str(row.get("item_id") or "")
        verdict = str(row.get("verdict") or "")
        if not item_id or verdict not in VALID_VERDICTS:
            return {"status": "unknown", "reason": "broken", "counts": counts, "path": str(path)}
        effective[item_id] = verdict
    for verdict in effective.values():
        counts[verdict] += 1
    return {"status": "known", "counts": counts, "path": str(path)}


def _git_tree_payload(workspace: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(workspace),
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "unknown", "reason": type(exc).__name__}
    if result.returncode != 0:
        return {"status": "unknown", "reason": (result.stderr or "").strip()}
    entries = [line for line in result.stdout.splitlines() if line.strip()]
    return {"status": "dirty" if entries else "clean", "porcelain_count": len(entries)}


def _select_next_safe_action(payload: dict[str, Any]) -> str:
    if payload.get("kill_switch", {}).get("active") is True:
        return NEXT_KILL_SWITCH
    if payload.get("git", {}).get("status") == "dirty":
        return NEXT_GIT_DIRTY
    approvals_pending = _count_value(payload.get("approvals", {}).get("pending"))
    if approvals_pending is not None and approvals_pending > 0:
        return NEXT_APPROVALS
    daemon = payload.get("daemon") or {}
    if _daemon_blocks_next_action(daemon):
        return NEXT_DAEMON
    grounded = payload.get("self_build", {}).get("grounded_target", {})
    if grounded.get("classification") == "off_allowlist":
        return NEXT_GROUNDED
    paused = _count_value(payload.get("tasks", {}).get("paused"))
    if paused is not None and paused > 0:
        return NEXT_PAUSED
    if daemon.get("interpretation") == "expected_without_scheduled_daemon":
        return NEXT_DAEMON_STALE_MANUAL
    return NEXT_DRY_RUN


def collect_dry_health_pass(
    workspace: Path,
    *,
    now: datetime | None = None,
    git_status_fn: GitStatusFn | None = None,
) -> dict[str, Any]:
    now = (now or _now_utc()).astimezone(timezone.utc)
    budget = _budget_payload(workspace, now)
    approvals = _count_status(workspace / APPROVAL_INBOX_PATH, "pending")
    tasks = _count_status(workspace / RUNTIME_TASKS_PATH, "paused")
    scheduler = _scheduler_due(workspace, now)
    daemon = _enrich_daemon_payload(_daemon_payload(workspace, now), scheduler)
    payload = {
        "command": ":dry-health-pass",
        "generated_at": now.isoformat(),
        "daemon": daemon,
        "approvals": {"pending": approvals.get("count"), **approvals},
        "tasks": {"paused": tasks.get("count"), **tasks},
        "scheduler": scheduler,
        "budget": {
            "status": budget.get("status"),
            "reason": budget.get("reason"),
            "hour": _window_by_name(budget, "hour"),
            "day": _window_by_name(budget, "day"),
        },
        "kill_switch": _kill_switch_payload(workspace, budget),
        "self_build": _self_build_payload(workspace, now),
        "value_reviews": _value_review_payload(workspace),
        "git": (git_status_fn or _git_tree_payload)(workspace),
    }
    payload["next_safe_action"] = _select_next_safe_action(payload)
    return payload


def _fmt_count(value: object) -> str:
    return str(value) if isinstance(value, int) else "unknown"


def _fmt_budget_window(window: dict[str, Any]) -> str:
    counters = window.get("counters") if isinstance(window, dict) else None
    if not isinstance(counters, dict) or window.get("status") != "known":
        return "unknown"
    names = ("llm_calls", "model_tokens", "model_cost_units", "web_fetches")
    parts = []
    for name in names:
        data = counters.get(name) or {}
        used = data.get("used")
        limit = data.get("limit")
        limit_text = "unlimited" if limit == 0 else str(limit)
        parts.append(f"{name}={used}/{limit_text}")
    return ", ".join(parts)


def _format_dry_health_pass(payload: dict[str, Any]) -> str:
    kill = payload.get("kill_switch") or {}
    cooldown = payload.get("self_build", {}).get("cooldown", {})
    grounded = payload.get("self_build", {}).get("grounded_target", {})
    value_counts = payload.get("value_reviews", {}).get("counts", {})
    git = payload.get("git") or {}
    daemon = payload.get("daemon") or {}
    daemon_line = f"daemon: {daemon.get('status', 'unknown')}"
    interpretation = daemon.get("interpretation")
    if interpretation:
        daemon_line += f" ({interpretation})"
    lines = [
        "=== dry health pass ===",
        daemon_line,
        f"approvals pending: {_fmt_count(payload.get('approvals', {}).get('pending'))}",
        f"tasks paused: {_fmt_count(payload.get('tasks', {}).get('paused'))}",
        f"scheduler due: {_fmt_count(payload.get('scheduler', {}).get('due'))}",
        f"budget hour: {_fmt_budget_window(payload.get('budget', {}).get('hour', {}))}",
        f"budget day: {_fmt_budget_window(payload.get('budget', {}).get('day', {}))}",
        (
            "kill-switch: "
            f"active={kill.get('active') if kill.get('active') is not None else 'unknown'} "
            f"reason={kill.get('reason') or 'none'}"
        ),
        (
            "self-build cooldown: "
            f"{cooldown.get('status', 'unknown')} "
            f"remaining_seconds={cooldown.get('remaining_seconds')}"
        ),
        (
            "self-build grounded target: "
            f"{grounded.get('classification', 'unknown')} "
            f"target={grounded.get('target_path') or 'none'}"
        ),
        "value reviews: "
        + ", ".join(f"{verdict}={value_counts.get(verdict, 0)}" for verdict in VALUE_VERDICTS),
        f"git tree: {git.get('status', 'unknown')}",
        f"next safe action: {payload.get('next_safe_action')}",
    ]
    return "\n".join(lines)


def _handle_dry_health_pass(rest: str, _agent: Any, workspace: Path) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :dry-health-pass [--json]", file=sys.stderr)
        return True
    payload = collect_dry_health_pass(Path(workspace))
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_dry_health_pass(payload), file=sys.stderr)
    return True
