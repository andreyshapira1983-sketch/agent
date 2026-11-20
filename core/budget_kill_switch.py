"""Persistent budget kill-switch for autonomous / daemon execution."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Default location of the latched kill-switch state, relative to a workspace.
KILL_SWITCH_PATH = "data/budget_kill_switch.json"

# Conservative day limits for the expensive counters when autonomous mode finds no positive
# configured limit.
# Safety nets, not targets: config/budget_limits.json or AGENT_BUDGET_DAY_* env overrides win.
CONSERVATIVE_DAY_LIMITS: dict[str, int] = {
    "llm_calls": 100,
    "model_tokens": 300_000,
    "model_cost_units": 500,
}

# The window the kill-switch guards; the hour window stays with the ledger reserve/check path.
_GUARDED_WINDOW = "day"

# Machine-readable reason when the switch is engaged; the daemon reuses it verbatim so operators can
# grep for it.
REASON_ENGAGED = "budget_kill_switch"

# `counter` of a latch that is on because its state file could not be read; `limit_source` carries
# the read error.
UNREADABLE_COUNTER = "state_file_unreadable"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


@dataclass
class KillSwitchState:
    """Snapshot of the kill-switch decision; the daemon gates only on ``active``, the rest tells an operator why."""

    active: bool = False
    reason: str = ""
    counter: str | None = None
    window: str | None = None
    used: int = 0
    limit: int = 0
    limit_source: str = ""  # "config" | "conservative_default" | ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "reason": self.reason,
            "counter": self.counter,
            "window": self.window,
            "used": self.used,
            "limit": self.limit,
            "limit_source": self.limit_source,
            "timestamp": self.timestamp,
        }

    @classmethod
    def inactive(cls) -> KillSwitchState:
        return cls(active=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> KillSwitchState:
        if not isinstance(data, Mapping):
            return cls.inactive()
        return cls(
            active=bool(data.get("active", False)),
            reason=str(data.get("reason", "") or ""),
            counter=(data.get("counter") if data.get("counter") is not None else None),
            window=(data.get("window") if data.get("window") is not None else None),
            used=_as_int(data.get("used", 0)),
            limit=_as_int(data.get("limit", 0)),
            limit_source=str(data.get("limit_source", "") or ""),
            timestamp=str(data.get("timestamp", "") or ""),
        )


def _day_counters(snapshot: Mapping[str, Any] | None) -> dict[str, dict[str, int]]:
    """Extract the ``day`` window counters from a ledger snapshot."""
    if not isinstance(snapshot, Mapping):
        return {}
    windows = snapshot.get("windows", [])
    if not isinstance(windows, list):
        return {}
    for window in windows:
        if not isinstance(window, Mapping) or window.get("name") != _GUARDED_WINDOW:
            continue
        counters = window.get("counters", {})
        if not isinstance(counters, Mapping):
            return {}
        out: dict[str, dict[str, int]] = {}
        for name, data in counters.items():
            if isinstance(data, Mapping):
                out[str(name)] = {
                    "used": _as_int(data.get("used", 0)),
                    "limit": _as_int(data.get("limit", 0)),
                }
        return out
    return {}


def evaluate_day_budget(
    snapshot: Mapping[str, Any] | None,
    *,
    conservative_limits: Mapping[str, int] = CONSERVATIVE_DAY_LIMITS,
    now_iso: str | None = None,
) -> KillSwitchState:
    """Pure decision: is the autonomous *day* budget exhausted?

    Expensive counters without a positive configured limit get a conservative default; the first counter (stable order) whose use reaches its limit trips the switch.
    """
    day = _day_counters(snapshot)

    # Guarded expensive counters plus explicitly configured day counters, in a deterministic order.
    ordered: list[str] = list(conservative_limits.keys())
    for name in sorted(day.keys()):
        if name not in ordered:
            ordered.append(name)

    for counter in ordered:
        data = day.get(counter, {"used": 0, "limit": 0})
        used = _as_int(data.get("used", 0))
        configured = _as_int(data.get("limit", 0))
        if configured > 0:
            effective, source = configured, "config"
        else:
            default = _as_int(conservative_limits.get(counter, 0))
            if default <= 0:
                continue  # unguarded counter with no configured limit
            effective, source = default, "conservative_default"
        if used >= effective:
            return KillSwitchState(
                active=True,
                reason=REASON_ENGAGED,
                counter=counter,
                window=_GUARDED_WINDOW,
                used=used,
                limit=effective,
                limit_source=source,
                timestamp=now_iso or _now_iso(),
            )
    return KillSwitchState.inactive()


def default_path(workspace: Path) -> Path:
    return workspace / KILL_SWITCH_PATH


@dataclass
class BudgetKillSwitch:
    """Persistence and latching around :func:`evaluate_day_budget`."""

    path: Path
    conservative_limits: Mapping[str, int] = field(
        default_factory=lambda: dict(CONSERVATIVE_DAY_LIMITS)
    )

    def load(self) -> KillSwitchState:
        """Return the latched state: inactive only when the file is ABSENT.

        A file that exists but cannot be read is a stop, not a pass (operator's
        decision 2026-09-25). Until then a corrupt latch read as "inactive", so
        a torn write or a stray byte released the brake it was meant to hold;
        a deny list that answers "allowed" for what it cannot parse is the
        failure the kill-switch literature names first. The operator clears it
        with `:budget-kill-switch --clear`, as any latch.
        """
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return KillSwitchState.inactive()
        except (OSError, ValueError) as exc:
            return self._unreadable(f"{type(exc).__name__}: {exc}")
        try:
            data = json.loads(raw)
        except ValueError:
            return self._unreadable("not JSON")
        if not isinstance(data, Mapping):
            return self._unreadable(f"JSON {type(data).__name__}, not an object")
        return KillSwitchState.from_dict(data)

    @staticmethod
    def _unreadable(detail: str) -> KillSwitchState:
        return KillSwitchState(active=True, reason=REASON_ENGAGED, counter=UNREADABLE_COUNTER,
                               limit_source=detail[:200], timestamp=_now_iso())

    def _write(self, state: KillSwitchState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)  # atomic swap

    def clear(self) -> None:
        """Operator reset: remove the latched state (no-op if absent); OSError if it stays."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def status(
        self,
        snapshot: Mapping[str, Any] | None,
        *,
        now_iso: str | None = None,
    ) -> KillSwitchState:
        """Read-only view: latched state if active, else a live evaluation. Never writes."""
        latched = self.load()
        if latched.active:
            return latched
        return evaluate_day_budget(
            snapshot,
            conservative_limits=self.conservative_limits,
            now_iso=now_iso,
        )

    def engage_if_needed(
        self,
        snapshot: Mapping[str, Any] | None,
        *,
        now_iso: str | None = None,
    ) -> KillSwitchState:
        """Daemon gate: latch on a fresh trip, honour an existing latch."""
        latched = self.load()
        if latched.active:
            return latched
        state = evaluate_day_budget(
            snapshot,
            conservative_limits=self.conservative_limits,
            now_iso=now_iso,
        )
        if state.active:
            self._write(state)
        return state
