"""Operator acknowledgement of advisory alerts: retire accepted signals without deleting them."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.ids import new_id
from core.state_integrity import read_state_jsonl, rewrite_state_jsonl

# Critical/high severities are excluded on purpose: real breakages must never be suppressible.
_SUPPRESSIBLE_SEVERITIES = frozenset({"medium", "low"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_suppressible_severity(severity: str) -> bool:
    """Whether an alert of this severity may be acknowledged (never critical/high)."""
    return str(severity) in _SUPPRESSIBLE_SEVERITIES


@dataclass(frozen=True)
class AlertAck:
    """A single operator acknowledgement of an advisory alert, optionally expiring."""

    action: str
    acknowledged_by: str = "operator"
    reason: str = ""
    expires_at: str | None = None
    id: str = field(default_factory=lambda: new_id("ack"))
    created_at: str = field(default_factory=_now_iso)

    def is_active(self, *, now: datetime | None = None) -> bool:
        """Whether this acknowledgement is still in force at ``now`` (malformed expiry counts as active)."""
        if not self.expires_at:
            return True
        moment = now or datetime.now(timezone.utc)
        try:
            exp = datetime.fromisoformat(self.expires_at)
        except (ValueError, TypeError):
            return True
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment < exp

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "action": self.action,
            "acknowledged_by": self.acknowledged_by,
            "reason": self.reason,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AlertAck:
        return cls(
            id=str(data.get("id") or new_id("ack")),
            action=str(data.get("action") or ""),
            acknowledged_by=str(data.get("acknowledged_by") or "operator"),
            reason=str(data.get("reason") or ""),
            expires_at=str(data.get("expires_at")) if data.get("expires_at") else None,
            created_at=str(data.get("created_at") or _now_iso()),
        )


def active_acknowledged_actions(
    acks: list[AlertAck],
    *,
    now: datetime | None = None,
) -> frozenset[str]:
    """Return the set of action names currently acknowledged and not expired (pure)."""
    moment = now or datetime.now(timezone.utc)
    return frozenset(
        ack.action
        for ack in acks
        if ack.action and ack.is_active(now=moment)
    )


@dataclass
class AlertAckStore:
    """Persistent, auditable store of operator acknowledgements (runtime state, state-integrity envelope)."""

    acks: list[AlertAck] = field(default_factory=list)
    path: Path | str | None = None

    def __post_init__(self) -> None:
        if self.path is not None:
            self.path = Path(self.path)
            self.acks = self._load()

    def acknowledge(
        self,
        *,
        action: str,
        acknowledged_by: str = "operator",
        reason: str = "",
        ttl_hours: float | None = None,
    ) -> AlertAck:
        """Acknowledge ``action``, replacing any existing ack for it."""
        action = str(action).strip()
        if not action:
            raise ValueError("action must be a non-empty alert identifier")
        expires_at: str | None = None
        if ttl_hours is not None and ttl_hours > 0:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(hours=float(ttl_hours))
            ).isoformat()
        ack = AlertAck(
            action=action,
            acknowledged_by=acknowledged_by,
            reason=reason,
            expires_at=expires_at,
        )
        self.acks = [a for a in self.acks if a.action != action]
        self.acks.append(ack)
        self._save()
        return ack

    def clear(self, action: str) -> int:
        """Un-acknowledge ``action``. Returns how many acks were removed."""
        action = str(action).strip()
        before = len(self.acks)
        self.acks = [a for a in self.acks if a.action != action]
        removed = before - len(self.acks)
        if removed:
            self._save()
        return removed

    def list_active(self, *, now: datetime | None = None) -> list[AlertAck]:
        moment = now or datetime.now(timezone.utc)
        return [a for a in self.acks if a.is_active(now=moment)]

    def active_actions(self, *, now: datetime | None = None) -> frozenset[str]:
        return active_acknowledged_actions(self.acks, now=now)

    def load(self) -> list[AlertAck]:
        return list(self.acks)

    def _load(self) -> list[AlertAck]:
        assert self.path is not None  # noqa: S101 — type narrowing, guarded above
        path = Path(self.path)
        if not path.exists():
            return list(self.acks)
        out: list[AlertAck] = []
        for raw in read_state_jsonl(path):
            try:
                out.append(AlertAck.from_dict(raw))
            except (TypeError, ValueError):
                continue
        return out

    def _save(self) -> None:
        if self.path is None:
            return
        rewrite_state_jsonl(Path(self.path), [a.to_dict() for a in self.acks])
