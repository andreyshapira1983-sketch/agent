"""Run ``agent_tick.py`` repeatedly inside a long-lived container.

The project daemon is intentionally a one-shot bounded tick. This wrapper keeps
Docker alive, starts one tick immediately, then sleeps until the next interval.
It never changes the tick's approval, budget, or dry-run policy.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_STOP = False


def _request_stop(signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True
    print(f"[docker-daemon] stop requested signal={signum}", flush=True)


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(1, value)


def _run_tick(workspace: Path, timeout_seconds: int) -> int:
    command = [sys.executable, str(workspace / "agent_tick.py"), "--workspace", str(workspace)]
    started = time.monotonic()
    print(f"[docker-daemon] tick start command={command!r}", flush=True)
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            check=False,
            timeout=timeout_seconds,
        )
        code = int(completed.returncode)
    except subprocess.TimeoutExpired:
        code = 124
        print(
            f"[docker-daemon] tick timeout after {timeout_seconds}s",
            file=sys.stderr,
            flush=True,
        )
    elapsed = time.monotonic() - started
    print(f"[docker-daemon] tick end exit_code={code} elapsed={elapsed:.1f}s", flush=True)
    return code


def _sleep_interruptibly(seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while not _STOP:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(1.0, remaining))


def _heartbeat_is_fresh(workspace: Path, max_age_seconds: int) -> bool:
    path = workspace / "data" / "daemon_heartbeat.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        stamp = datetime.fromisoformat(str(payload["ts"]))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    return 0 <= age <= max_age_seconds


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Long-lived Docker wrapper for agent_tick.py")
    parser.add_argument("--workspace", default=os.environ.get("AGENT_WORKSPACE", "/workspace"))
    parser.add_argument("--healthcheck", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    interval = _positive_int("AGENT_TICK_INTERVAL_SECONDS", 1800)
    timeout = _positive_int("AGENT_DOCKER_TICK_TIMEOUT_SECONDS", max(60, interval - 60))

    if args.healthcheck:
        return 0 if _heartbeat_is_fresh(workspace, interval * 2 + 120) else 1

    if not (workspace / "agent_tick.py").is_file():
        print(f"[docker-daemon] agent_tick.py not found in {workspace}", file=sys.stderr)
        return 2

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    print(
        f"[docker-daemon] started workspace={workspace} interval={interval}s "
        f"tick_timeout={timeout}s dry_run={os.environ.get('AGENT_TICK_DRY_RUN', '1')}",
        flush=True,
    )

    while not _STOP:
        cycle_started = time.monotonic()
        _run_tick(workspace, timeout)
        elapsed = time.monotonic() - cycle_started
        _sleep_interruptibly(max(0.0, interval - elapsed))

    print("[docker-daemon] stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
