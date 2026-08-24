"""Persistent budget windows for long-running autonomous work."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.budget_governor import budget_limit_label as _budget_limit_label
from core.state_integrity import append_state_jsonl, read_state_jsonl

#: Starting size of the per-call bounded tail read (MIR-125). Doubles until
#: the read provably covers the longest window; the whole file is the last
#: resort, never the default.
_TAIL_READ_BYTES = 65536

BudgetLedgerCounter = str

DEFAULT_COUNTERS = (
    "llm_calls",
    "model_tokens",
    "model_cost_units",
    "web_fetches",
    "cycles",
    "team_model_calls",
    "team_cost_units",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _parse_ts(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _env_int(name: str, default: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(0, int(raw.strip()))
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc


@dataclass(frozen=True)
class BudgetWindow:
    name: str
    seconds: int
    limits: dict[BudgetLedgerCounter, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name or not self.name.isascii():
            raise ValueError("budget window name must be non-empty ASCII")
        if self.seconds < 1:
            raise ValueError("budget window seconds must be >= 1")
        for counter, limit in self.limits.items():
            if not counter or int(limit) < 0:
                raise ValueError("budget window limits must be non-negative")

    def limit_for(self, counter: BudgetLedgerCounter) -> int:
        return int(self.limits.get(counter, 0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "seconds": self.seconds,
            "limits": dict(self.limits),
        }


@dataclass(frozen=True)
class BudgetLedgerRecord:
    counter: BudgetLedgerCounter
    amount: int
    reason: str = ""
    scope: str = "general"
    created_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if not self.counter:
            raise ValueError("budget counter must be non-empty")
        if self.amount < 1:
            raise ValueError("budget amount must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "counter": self.counter,
            "amount": self.amount,
            "reason": self.reason,
            "scope": self.scope,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BudgetLedgerRecord:
        return cls(
            counter=str(data["counter"]),
            amount=max(1, int(data["amount"])),
            reason=str(data.get("reason") or ""),
            scope=str(data.get("scope") or "general"),
            created_at=str(data.get("created_at") or _utc_now_iso()),
        )


@dataclass(frozen=True)
class BudgetLedgerDecision:
    counter: BudgetLedgerCounter
    allowed: bool
    amount: int
    reason: str
    window: str | None = None
    used: int = 0
    limit: int = 0

    def to_dict(self) -> dict[str, Any]:
        limit_label = _budget_limit_label(self.limit)
        return {
            "counter": self.counter,
            "allowed": self.allowed,
            "amount": self.amount,
            "reason": self.reason,
            "window": self.window,
            "used": self.used,
            "limit": self.limit,
            "limit_label": limit_label,
            "limit_enforced": self.limit > 0,
        }


@dataclass
class BudgetLedger:
    path: Path | None = None
    windows: tuple[BudgetWindow, ...] = ()
    logger: Any | None = None
    config_path: Path | None = None
    records: list[BudgetLedgerRecord] = field(default_factory=list)

    @classmethod
    def from_env(
        cls,
        *,
        path: Path | None = None,
        logger: Any | None = None,
        config_path: Path | None = None,
    ) -> BudgetLedger:
        resolved_config = _budget_config_path(config_path)
        return cls(
            path=path,
            windows=_windows_from_env(config_path=resolved_config),
            logger=logger,
            config_path=resolved_config,
        )

    def load_records(self) -> list[BudgetLedgerRecord]:
        if self.path is None or not self.path.exists():
            return list(self.records)
        loaded: list[BudgetLedgerRecord] = []
        for data in read_state_jsonl(self.path):
            try:
                loaded.append(BudgetLedgerRecord.from_dict(data))
            except (KeyError, TypeError, ValueError):
                continue
        return loaded

    def _records_for_windows(self, now: datetime) -> list[BudgetLedgerRecord]:
        """Records that can matter to any window — a BOUNDED tail read.

        MIR-125: `reserve()` used to call `load_records()`, re-reading the
        entire append-only ledger, and MIR-116 wired a reservation into the
        path of every model call — so the cost gate was O(file size) on a file
        that only grows, in exactly the long-run scenario it was added for.

        Windows are time-bounded, so only records newer than the longest
        window's cutoff can count. The asymmetry that shapes this: reading too
        much is merely slow, while reading too little UNDERCOUNTS usage and
        lets the agent spend past its limit. So the tail read PROVES its
        coverage — it doubles until the oldest record it has seen is older
        than the cutoff, or until it has consumed the whole file — and any
        read failure falls back to the full read rather than to a guess.
        """
        if self.path is None or not self.path.exists():
            return list(self.records)
        longest = max((w.seconds for w in self.windows), default=0)
        if longest <= 0:
            return self.load_records()
        cutoff = now - timedelta(seconds=longest)
        size = self.path.stat().st_size
        span = _TAIL_READ_BYTES
        while True:
            if span >= size:
                return self.load_records()
            try:
                with self.path.open("rb") as fh:
                    fh.seek(size - span)
                    chunk = fh.read().decode("utf-8", errors="replace")
            except OSError:
                return self.load_records()
            lines = chunk.splitlines()[1:]  # the first line may be cut in half
            records: list[BudgetLedgerRecord] = []
            oldest: datetime | None = None
            for line in lines:
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue
                payload = raw.get("payload", raw)
                if not isinstance(payload, dict):
                    continue
                try:
                    record = BudgetLedgerRecord.from_dict(payload)
                except (KeyError, TypeError, ValueError):
                    continue
                records.append(record)
                created = _parse_ts(record.created_at)
                if created is not None and (oldest is None or created < oldest):
                    oldest = created
            # Coverage proof: the tail must reach STRICTLY past the cutoff, so
            # no in-window record can be sitting just above the read boundary.
            if oldest is not None and oldest < cutoff:
                return records
            span *= 2

    def reserve(
        self,
        counter: BudgetLedgerCounter,
        *,
        amount: int = 1,
        reason: str = "",
        scope: str = "general",
        now: datetime | None = None,
    ) -> BudgetLedgerDecision:
        if amount < 1:
            raise ValueError("budget amount must be >= 1")
        now = now or _utc_now()
        records = self._records_for_windows(now)
        for window in self.windows:
            limit = window.limit_for(counter)
            if limit <= 0:
                continue
            used = _used_in_window(records, counter=counter, window=window, now=now)
            if used + amount > limit:
                decision = BudgetLedgerDecision(
                    counter=counter,
                    allowed=False,
                    amount=amount,
                    reason=(
                        reason
                        or f"persistent {window.name} budget exhausted for {counter}"
                    ),
                    window=window.name,
                    used=used,
                    limit=limit,
                )
                _log(self.logger, "budget_window_blocked", decision.to_dict())
                return decision
        self.record(counter, amount=amount, reason=reason, scope=scope, now=now)
        decision = BudgetLedgerDecision(
            counter=counter,
            allowed=True,
            amount=amount,
            reason=reason,
        )
        _log(self.logger, "budget_window_reserved", decision.to_dict())
        return decision

    def check(
        self,
        counter: BudgetLedgerCounter,
        *,
        amount: int = 1,
        reason: str = "",
        now: datetime | None = None,
    ) -> BudgetLedgerDecision:
        """Evaluate whether *amount* would fit inside every window WITHOUT
        recording anything. Used for pre-flight cost estimates where the actual
        usage is recorded separately once the call completes."""
        if amount < 1:
            raise ValueError("budget amount must be >= 1")
        now = now or _utc_now()
        records = self._records_for_windows(now)
        for window in self.windows:
            limit = window.limit_for(counter)
            if limit <= 0:
                continue
            used = _used_in_window(records, counter=counter, window=window, now=now)
            if used + amount > limit:
                return BudgetLedgerDecision(
                    counter=counter,
                    allowed=False,
                    amount=amount,
                    reason=(
                        reason
                        or f"persistent {window.name} budget would exhaust for {counter}"
                    ),
                    window=window.name,
                    used=used,
                    limit=limit,
                )
        return BudgetLedgerDecision(
            counter=counter,
            allowed=True,
            amount=amount,
            reason=reason,
        )

    def record(
        self,
        counter: BudgetLedgerCounter,
        *,
        amount: int,
        reason: str = "",
        scope: str = "general",
        now: datetime | None = None,
    ) -> BudgetLedgerRecord:
        if amount < 1:
            raise ValueError("budget amount must be >= 1")
        record = BudgetLedgerRecord(
            counter=counter,
            amount=amount,
            reason=reason,
            scope=scope,
            created_at=(now or _utc_now()).isoformat(),
        )
        self.records.append(record)
        if self.path is not None:
            append_state_jsonl(self.path, [record.to_dict()])
        _log(self.logger, "budget_window_recorded", record.to_dict())
        return record

    def snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or _utc_now()
        records = self.load_records()
        windows_payload = []
        for window in self.windows:
            counters = sorted(set(DEFAULT_COUNTERS) | set(window.limits))
            windows_payload.append({
                "name": window.name,
                "seconds": window.seconds,
                "counters": {
                    counter: {
                        "used": _used_in_window(
                            records,
                            counter=counter,
                            window=window,
                            now=now,
                        ),
                        "limit": window.limit_for(counter),
                    }
                    for counter in counters
                },
            })
        totals: dict[str, int] = {}
        for record in records:
            totals[record.counter] = totals.get(record.counter, 0) + record.amount
        return {
            "path": str(self.path) if self.path is not None else None,
            "config_path": str(self.config_path) if self.config_path is not None else None,
            "windows": windows_payload,
            "totals": totals,
            "recent": [record.to_dict() for record in records[-10:]],
        }

    def user_summary(self) -> str:
        snapshot = self.snapshot()
        lines = ["=== persistent budget windows ==="]
        lines.append(f"path: {snapshot['path']}")
        if not snapshot["windows"]:
            lines.append("(no persistent budget windows configured)")
            return "\n".join(lines)
        for window in snapshot["windows"]:
            lines.append(f"window: {window['name']} ({window['seconds']}s)")
            for counter, data in window["counters"].items():
                limit = data["limit"]
                if limit <= 0 and data["used"] == 0:
                    continue
                limit_text = str(limit) if limit > 0 else "unlimited"
                lines.append(f"  {counter}: {data['used']}/{limit_text}")
        return "\n".join(lines)


def _windows_from_env(*, config_path: Path | None = None) -> tuple[BudgetWindow, ...]:
    config = _load_budget_config(config_path)
    hour_limits = _limits_from_env(
        "AGENT_BUDGET_HOUR",
        base=_limits_from_config(config, "hour"),
    )
    day_limits = _limits_from_env(
        "AGENT_BUDGET_DAY",
        base=_limits_from_config(config, "day"),
    )
    return (
        BudgetWindow("hour", 60 * 60, hour_limits),
        BudgetWindow("day", 24 * 60 * 60, day_limits),
    )


def _limits_from_env(
    prefix: str,
    *,
    base: dict[BudgetLedgerCounter, int] | None = None,
) -> dict[BudgetLedgerCounter, int]:
    limits = {counter: int((base or {}).get(counter, 0)) for counter in DEFAULT_COUNTERS}
    env_names = {
        "llm_calls": f"{prefix}_LLM_CALLS",
        "model_tokens": f"{prefix}_MODEL_TOKENS",
        "model_cost_units": f"{prefix}_MODEL_COST_UNITS",
        "web_fetches": f"{prefix}_WEB_FETCHES",
        "cycles": f"{prefix}_CYCLES",
        "team_model_calls": f"{prefix}_TEAM_MODEL_CALLS",
        "team_cost_units": f"{prefix}_TEAM_COST_UNITS",
    }
    for counter, env_name in env_names.items():
        if os.getenv(env_name) is not None:
            limits[counter] = _env_int(env_name)
    return limits


def _budget_config_path(config_path: Path | None) -> Path | None:
    raw = os.getenv("AGENT_BUDGET_CONFIG_PATH")
    if raw and raw.strip():
        return Path(raw.strip()).expanduser().resolve()
    if config_path is None:
        return None
    return Path(config_path).resolve()


def _load_budget_config(config_path: Path | None) -> dict[str, Any]:
    if config_path is None or not config_path.exists():
        # Отсутствие файла НЕ ошибка на этом слое: путь называют все
        # вызывающие по умолчанию, поэтому «назван» не значит «оператор его
        # завёл» — попытка различить их здесь покраснила 168 тестов, и
        # правильно сделала. Ожидание «потолок настроен» принадлежит живому
        # входу, и проверяется в `agent_tick._require_budget_config`
        # (H-33 в docs/audit/HISTORICAL_FAILURE_LEDGER.md).
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"budget config is not valid JSON: {config_path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"budget config must be a JSON object: {config_path}")
    return data


def _limits_from_config(
    config: dict[str, Any],
    window_name: str,
) -> dict[BudgetLedgerCounter, int]:
    windows = config.get("windows", config)
    if not isinstance(windows, dict):
        return {}
    raw_limits = windows.get(window_name, {})
    if not isinstance(raw_limits, dict):
        raise ValueError(f"budget config window {window_name!r} must be an object")
    limits: dict[BudgetLedgerCounter, int] = {}
    for counter in DEFAULT_COUNTERS:
        if counter not in raw_limits:
            continue
        try:
            limits[counter] = max(0, int(raw_limits[counter]))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"budget config {window_name}.{counter} must be a non-negative integer"
            ) from exc
    return limits


def _used_in_window(
    records: list[BudgetLedgerRecord],
    *,
    counter: BudgetLedgerCounter,
    window: BudgetWindow,
    now: datetime,
) -> int:
    cutoff = now - timedelta(seconds=window.seconds)
    total = 0
    for record in records:
        if record.counter != counter:
            continue
        created = _parse_ts(record.created_at)
        if created is None or created < cutoff:
            continue
        total += record.amount
    return total


def _log(logger: Any | None, event: str, payload: dict[str, Any]) -> None:
    if logger is not None:
        logger.log(event, payload)
