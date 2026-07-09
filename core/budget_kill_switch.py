"""Persistent budget kill-switch for autonomous / daemon execution (TD-022).

Autonomous runs must be *safe by default*. The persistent budget ledger
(:mod:`core.budget_ledger`) already tracks hour/day spend, but it treats a
missing or all-zero limit as *unlimited* — which is exactly the wrong default
for an unattended daemon that can otherwise keep spending after the day budget
is exhausted.

This module adds two guarantees on top of the existing ledger, WITHOUT changing
routing, provider catalogs, file-write / shell policies, or raising any limit:

1. **Budget-on-by-default.** In autonomous mode a conservative *day* limit is
   applied to the money/compute-heavy counters whenever no positive limit is
   configured, so "no config" never means "unlimited".
2. **Persistent kill-switch.** When a day-window counter reaches its effective
   limit the switch latches to a small JSON state file. Once latched it stays
   active across process restarts until an operator clears it, so repeated
   ticks cannot keep spending.

The evaluation logic is a pure function so it is trivially testable; the
:class:`BudgetKillSwitch` class only adds persistence (load / latch / clear) and
a read-only status view.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

# Default location of the latched kill-switch state, relative to a workspace.
KILL_SWITCH_PATH = "data/budget_kill_switch.json"

# Conservative *day* limits applied to the expensive counters when autonomous
# mode finds no positive configured limit. These are safety nets, not targets —
# any positive value in config/budget_limits.json (or the AGENT_BUDGET_DAY_*
# env overrides) takes precedence. Kept in line with the documented example
# day budget so behaviour is predictable.
CONSERVATIVE_DAY_LIMITS: dict[str, int] = {
    "llm_calls": 100,
    "model_tokens": 300_000,
    "model_cost_units": 500,
}

# The window the kill-switch guards. Day-level exhaustion is the hard stop; the
# hour window remains handled by the existing ledger reserve/check path.
_GUARDED_WINDOW = "day"

# Machine-readable reason emitted when the switch is engaged. The daemon reuses
# this verbatim as its heartbeat/report reason so operators can grep for it.
REASON_ENGAGED = "budget_kill_switch"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


@dataclass
class KillSwitchState:
    """Snapshot of the kill-switch decision.

    ``active`` is the only field the daemon must gate on; the remainder give an
    operator the *why* (triggering counter, window, used/limit, when).
    """

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
    def inactive(cls) -> "KillSwitchState":
        return cls(active=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "KillSwitchState":
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

    A conservative default limit is applied to each expensive counter that lacks
    a positive configured limit, so an all-zero / missing config never behaves
    as unlimited in autonomous mode. The switch trips as soon as any guarded
    counter's used amount reaches its effective limit. Counters are examined in
    a stable order (configured expensive counters first) and the first trip
    wins.
    """
    day = _day_counters(snapshot)

    # Union of the guarded expensive counters and any explicitly-configured day
    # counters, evaluated in a deterministic order.
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
    """Persistence + latching around :func:`evaluate_day_budget`.

    Once engaged the state is written to :attr:`path` and stays active across
    restarts until :meth:`clear` is called, so an exhausted day budget cannot be
    silently reset by simply starting a new process.
    """

    path: Path
    conservative_limits: Mapping[str, int] = field(
        default_factory=lambda: dict(CONSERVATIVE_DAY_LIMITS)
    )

    def load(self) -> KillSwitchState:
        """Return the latched state, or inactive when absent/corrupt."""
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return KillSwitchState.inactive()
        try:
            data = json.loads(raw)
        except ValueError:
            return KillSwitchState.inactive()
        return KillSwitchState.from_dict(data)

    def _write(self, state: KillSwitchState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)  # atomic swap

    def clear(self) -> None:
        """Operator reset — remove the latched state (no-op if absent)."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def status(
        self,
        snapshot: Mapping[str, Any] | None,
        *,
        now_iso: str | None = None,
    ) -> KillSwitchState:
        """Read-only view: latched state if active, else a live evaluation.

        Never writes, so it is safe for interactive status commands.
        """
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
        """Daemon gate: latch on a fresh trip, honour an existing latch.

        Returns the effective state. If already latched active, the original
        latch (and its timestamp) is preserved. If a live evaluation trips for
        the first time, the state is persisted before returning.
        """
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
