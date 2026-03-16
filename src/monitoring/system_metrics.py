"""
Real system metrics snapshot with explicit source and timestamp.

Primary source: psutil (if available). The module returns a stable schema even when
metrics are unavailable, so callers can enforce anti-simulation policies.
"""
from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any


_CACHE: dict[str, Any] = {
    "snapshot": None,
    "monotonic": 0.0,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _collect_with_psutil(top_n: int) -> dict[str, Any] | None:
    try:
        import psutil  # type: ignore
    except ImportError:
        return None

    try:
        # Warm-up call then short interval gives meaningful CPU percentage.
        cpu_percent = float(psutil.cpu_percent(interval=0.15))
        ram_percent = float(psutil.virtual_memory().percent)

        processes: list[dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            try:
                info = proc.info
                rss = getattr(info.get("memory_info"), "rss", 0)
                processes.append(
                    {
                        "pid": int(info.get("pid") or 0),
                        "name": str(info.get("name") or "unknown")[:120],
                        "cpu_percent": float(info.get("cpu_percent") or 0.0),
                        "rss_mb": round(float(rss) / (1024 * 1024), 2),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue

        processes.sort(key=lambda p: p["cpu_percent"], reverse=True)
        return {
            "ok": True,
            "source": "psutil",
            "timestamp_utc": _utc_now_iso(),
            "cpu_percent": round(cpu_percent, 2),
            "ram_percent": round(ram_percent, 2),
            "top_processes": processes[: max(1, top_n)],
        }
    except (psutil.Error, OSError, ValueError) as e:
        return {
            "ok": False,
            "source": "psutil",
            "timestamp_utc": _utc_now_iso(),
            "error": f"{e!s}"[:200],
            "cpu_percent": None,
            "ram_percent": None,
            "top_processes": [],
        }


def get_system_metrics_snapshot(*, force_refresh: bool = False, ttl_sec: int = 5, top_n: int = 5) -> dict[str, Any]:
    """
    Return system metrics snapshot with explicit source and timestamp.

    Schema is stable:
    - ok: bool
    - source: str
    - timestamp_utc: str
    - cpu_percent: float | None
    - ram_percent: float | None
    - top_processes: list[{pid, name, cpu_percent, rss_mb}]
    - error: str (optional)
    """
    now_mono = time.monotonic()
    cached = _CACHE.get("snapshot")
    cached_at = float(_CACHE.get("monotonic") or 0.0)
    if not force_refresh and cached and (now_mono - cached_at) <= max(0, ttl_sec):
        return dict(cached)

    snapshot = _collect_with_psutil(top_n=top_n)
    if snapshot is None:
        snapshot = {
            "ok": False,
            "source": "unavailable",
            "timestamp_utc": _utc_now_iso(),
            "error": "psutil is not installed; real system metrics are unavailable",
            "cpu_percent": None,
            "ram_percent": None,
            "top_processes": [],
        }

    _CACHE["snapshot"] = dict(snapshot)
    _CACHE["monotonic"] = now_mono
    return dict(snapshot)


def get_last_snapshot() -> dict[str, Any] | None:
    """Return cached snapshot if present (without refresh)."""
    snap = _CACHE.get("snapshot")
    if not snap:
        return None
    return dict(snap)
