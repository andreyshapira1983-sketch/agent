"""Operator acknowledgement for advisory alerts — retire accepted signals.

Some priority-intelligence alerts are *absorbing*: they never self-clear while
a chosen policy holds. The canonical example is ``review_dry_run_stall`` — every
dry-run tick only grows the streak, so the alert permanently wins the top spot
and masks the next real action even after the operator has consciously decided
"yes, staying dry-run is intentional right now".

An **acknowledgement** lets the operator say exactly that: *I have seen this
advisory signal and accept the current state*, so it should stop DOMINATING the
recommendation — without ever deleting it. The signal is still computed and
still reported; it is simply moved out of the top-pick race until either it is
explicitly un-acknowledged, the acknowledgement expires, or (for stall) effects
are actually enabled.

Two hard safety rules keep this honest, not a mute button:

* **Only advisory alerts can be acknowledged.** Objective breakages
  (daemon down, tick error, failing/inconclusive tests — severity
  ``critical``/``high``) are NEVER suppressible. You cannot acknowledge away a
  real failure; you can only quiet a soft nudge.
* **Acknowledgement is recorded, never hidden.** Every ack carries who, why and
  when, persists as auditable runtime state, and the suppressed alerts are still
  surfaced (as a note) by the selector's fallback.

The suppression decision itself (:func:`active_acknowledged_actions`) is PURE —
it takes acknowledgements + a clock and returns the set of currently-active
acknowledged action names. Persistence lives in :class:`AlertAckStore`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from core.ids import new_id
from core.state_integrity import read_state_jsonl, rewrite_state_jsonl


# Severities that an operator is allowed to acknowledge. Objective breakages
# (critical/high) are deliberately EXCLUDED — they must never be suppressible.
_SUPPRESSIBLE_SEVERITIES = frozenset({"medium", "low"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_suppressible_severity(severity: str) -> bool:
    """Whether an alert of this severity may be acknowledged at all.

    Pure. ``critical`` and ``high`` always return ``False`` so a real breakage
    can never be acknowledged away.
    """
    return str(severity) in _SUPPRESSIBLE_SEVERITIES


@dataclass(frozen=True)
class AlertAck:
    """A single operator acknowledgement of an advisory alert.

    ``action`` is the :class:`~core.best_next_action.BestNextAction.action`
    identifier (e.g. ``"review_dry_run_stall"``). ``expires_at`` is optional —
    when set, the ack stops being active after that instant so a stale "I'll
    look later" never silences the signal forever.
    """

    action: str
    acknowledged_by: str = "operator"
    reason: str = ""
    expires_at: Optional[str] = None
    id: str = field(default_factory=lambda: new_id("ack"))
    created_at: str = field(default_factory=_now_iso)

    def is_active(self, *, now: Optional[datetime] = None) -> bool:
        """Whether this acknowledgement is still in force at ``now``.

        Pure. An ack with no ``expires_at`` is active indefinitely; one with a
        malformed timestamp is treated as active (fail-open on the *display*
        side is harmless — the signal is only quieted, never deleted).
        """
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
    def from_dict(cls, data: dict) -> "AlertAck":
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
    now: Optional[datetime] = None,
) -> frozenset[str]:
    """Return the set of action names currently acknowledged (and not expired).

    Pure: no I/O, no mutation. Empty/None-action acks are ignored. This is the
    only value :func:`core.best_next_action.select_best_next_action` needs to
    apply suppression, keeping the selector itself free of persistence concerns.
    """
    moment = now or datetime.now(timezone.utc)
    return frozenset(
        ack.action
        for ack in acks
        if ack.action and ack.is_active(now=moment)
    )


@dataclass
class AlertAckStore:
    """Persistent, auditable store of operator acknowledgements.

    Runtime state (gitignored, like the rest of ``data/*.jsonl``). Mirrors the
    ``ApprovalInbox`` persistence convention: rows are wrapped in the
    state-integrity envelope via ``read_state_jsonl``/``rewrite_state_jsonl``.
    """

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
        ttl_hours: Optional[float] = None,
    ) -> AlertAck:
        """Acknowledge ``action``, replacing any existing ack for it.

        A fresh acknowledgement supersedes a prior one for the same action so
        the operator's most recent decision (and any new TTL) wins, rather than
        leaving stale duplicates to reason about.
        """
        action = str(action).strip()
        if not action:
            raise ValueError("action must be a non-empty alert identifier")
        expires_at: Optional[str] = None
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

    def list_active(self, *, now: Optional[datetime] = None) -> list[AlertAck]:
        moment = now or datetime.now(timezone.utc)
        return [a for a in self.acks if a.is_active(now=moment)]

    def active_actions(self, *, now: Optional[datetime] = None) -> frozenset[str]:
        return active_acknowledged_actions(self.acks, now=now)

    def load(self) -> list[AlertAck]:
        return list(self.acks)

    def _load(self) -> list[AlertAck]:
        assert self.path is not None
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
