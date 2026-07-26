"""Daemon liveness record — write, read, age, staleness.

These four functions used to live in ``agent_tick.py`` and were reached from
``core/campaign_io.py`` as ``agent_tick._read_heartbeat`` &c. That made the core
depend on an entry point: the one decision layer the architecture says imports
nothing from ``cli/``, ``app/`` or the tick script was reaching *up* into a cron
script for private helpers. Whether the daemon is alive is an input to a
decision (`best_next_action` asks it), so the reading belongs here; the tick
script keeps owning *when* to write.

``agent_tick`` re-exports these under their old private names, so existing
callers and tests are unaffected.
"""
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
