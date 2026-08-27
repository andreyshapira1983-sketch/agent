"""Daemon liveness record — write, read, age, staleness."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

#: Location of the liveness record, relative to the workspace.
HEARTBEAT_PATH = "data/daemon_heartbeat.json"

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


def write_heartbeat(workspace: Path, payload: dict) -> None:
    """Persist the latest daemon liveness record (overwrites previous).

    A single small JSON file gives O(1) staleness checks without scanning the
    append-only tick log. Always stamped with the current time.
    """
    hb_path = Path(workspace) / HEARTBEAT_PATH
    hb_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": _now_iso(), **payload}
    tmp = hb_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    tmp.replace(hb_path)  # atomic swap


def read_heartbeat(workspace: Path) -> dict | None:
    hb_path = Path(workspace) / HEARTBEAT_PATH
    if not hb_path.exists():
        return None
    try:
        return json.loads(hb_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def heartbeat_age_seconds(
    heartbeat: dict | None, *, now: datetime | None = None
) -> float | None:
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


def is_stale(age_seconds: float | None) -> bool:
    """True when the daemon has not ticked within the staleness window."""
    if age_seconds is None:
        return True
    return age_seconds > EXPECTED_TICK_INTERVAL_SECONDS * STALENESS_FACTOR


#: Location of the append-only per-tick outcome journal, relative to the
#: workspace. Owned here so writer (agent_tick._log_tick) and reader
#: (error_tick_streak) share one path — two spellings would rot apart.
TICK_LOG_RELPATH = Path("logs") / "daemon_tick.jsonl"

#: Consecutive `tick_error` ticks that constitute a suspected crash loop.
#: 3 on the 4-hour schedule = 12 hours of failing while the heartbeat stays
#: fresh — the heartbeat is written BEFORE the tick's work, so a daemon that
#: crashes every tick reports `alive` forever without this counter (MIR-135).
CRASH_LOOP_THRESHOLD = 3


def tick_log_path(workspace: Path) -> Path:
    return Path(workspace) / TICK_LOG_RELPATH


def error_tick_streak(workspace: Path) -> int:
    """Consecutive most-recent ticks that ended in `tick_error`.

    `tick_complete` and `budget_kill_switch` reset the streak: the first is a
    healthy tick, the second is deliberate throttling, not failure. A missing
    journal is 0 — no record is not a crash loop — and a broken line is
    skipped, not counted either way.
    """
    path = tick_log_path(workspace)
    if not path.is_file():
        return 0
    streak = 0
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                event = json.loads(raw).get("event")
            except ValueError:
                continue
            if event == "tick_error":
                streak += 1
            elif event in ("tick_complete", "budget_kill_switch"):
                streak = 0
    except OSError:
        return 0
    return streak
