"""Incident Handling skeleton (§7 Security — Incident Handling / B-04 Safety).

The last brick of the CORE-0 Boundary Layer. When something goes wrong during
autonomous operation (a tool keeps failing, the circuit breaker trips, the
daemon heartbeat goes stale, a verification collapses), the agent should not
just log a line and move on — it should open a structured **incident** that a
human can later read, understand, and close.

This is deliberately a *skeleton*: a minimal, honest record with the seven
fields the operator specified, plus a small append-only store. It does NOT try
to auto-remediate, page anyone, or run a postmortem. It captures the fact, its
severity, what (if anything) was done to contain it, whether a human must look,
and a place to write the lesson afterwards.

Design principles (mirror core/alert_ack.py + core/approval_inbox.py)
---------------------------------------------------------------------
* Pure record + thin persistence. No LLM, no network.
* Append-mostly: incidents are opened, then their status/postmortem updated in
  place. History is never silently deleted.
* Persisted as runtime state (gitignored ``data/*.jsonl``) through the same
  state-integrity envelope as the other stores.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from core.ids import new_id
from core.state_integrity import read_state_jsonl, rewrite_state_jsonl


IncidentSeverity = Literal["critical", "high", "medium", "low"]
IncidentStatus = Literal["open", "contained", "escalated", "resolved"]

_VALID_SEVERITIES = {"critical", "high", "medium", "low"}
_VALID_STATUSES = {"open", "contained", "escalated", "resolved"}

# Severities that always demand a human look, regardless of containment.
_MUST_ESCALATE_SEVERITIES = frozenset({"critical", "high"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Incident:
    """A single structured incident record (the seven operator fields).

    * ``trigger`` — what raised it (e.g. ``"circuit_breaker_open"``).
    * ``affected_module`` — best-known blast radius (e.g. ``"core.loop"``).
    * ``containment_action`` — what was done to limit damage, if anything
      (e.g. ``"halted outbound tool calls"``); empty when nothing yet.
    * ``human_escalation`` — whether a human must review. Forced ``True`` for
      critical/high severity so a real breakage can never be auto-closed.
    * ``postmortem_note`` — filled in after resolution; the lesson learned.
    """

    severity: IncidentSeverity
    trigger: str
    affected_module: str = ""
    containment_action: str = ""
    human_escalation: bool = False
    postmortem_note: str = ""
    status: IncidentStatus = "open"
    id: str = field(default_factory=lambda: new_id("inc"))
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        if self.severity not in _VALID_SEVERITIES:
            raise ValueError(f"invalid incident severity: {self.severity}")
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"invalid incident status: {self.status}")
        if not str(self.trigger).strip():
            raise ValueError("incident trigger must be non-empty")
        # Critical/high incidents ALWAYS require a human — never auto-suppress.
        if self.severity in _MUST_ESCALATE_SEVERITIES and not self.human_escalation:
            object.__setattr__(self, "human_escalation", True)

    @property
    def is_open(self) -> bool:
        return self.status in ("open", "contained", "escalated")

    @property
    def needs_human(self) -> bool:
        """A human still owes this incident a look (escalated and not resolved)."""
        return self.human_escalation and self.status != "resolved"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "severity": self.severity,
            "trigger": self.trigger,
            "affected_module": self.affected_module,
            "containment_action": self.containment_action,
            "human_escalation": self.human_escalation,
            "postmortem_note": self.postmortem_note,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Incident":
        severity = str(data.get("severity") or "")
        status = str(data.get("status") or "open")
        if severity not in _VALID_SEVERITIES:
            raise ValueError(f"invalid incident severity: {severity}")
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid incident status: {status}")
        return cls(
            id=str(data.get("id") or new_id("inc")),
            severity=severity,  # type: ignore[arg-type]
            trigger=str(data.get("trigger") or ""),
            affected_module=str(data.get("affected_module") or ""),
            containment_action=str(data.get("containment_action") or ""),
            human_escalation=bool(data.get("human_escalation", False)),
            postmortem_note=str(data.get("postmortem_note") or ""),
            status=status,  # type: ignore[arg-type]
            created_at=str(data.get("created_at") or _now_iso()),
            updated_at=str(data.get("updated_at") or data.get("created_at") or _now_iso()),
        )


def summarise_incidents(incidents: list[Incident]) -> str:
    """One-line operator digest. PURE — no I/O.

    e.g. ``"incidents: 5 total | open=2 escalated=1 resolved=2 | "
    "by_severity: critical=1 high=1 medium=2 low=1 | needs_human=2"``.
    """
    if not incidents:
        return "incidents: none recorded"
    by_status: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    needs_human = 0
    for inc in incidents:
        by_status[inc.status] = by_status.get(inc.status, 0) + 1
        by_severity[inc.severity] = by_severity.get(inc.severity, 0) + 1
        if inc.needs_human:
            needs_human += 1
    status_part = " ".join(
        f"{name}={by_status[name]}" for name in sorted(by_status)
    )
    sev_order = ["critical", "high", "medium", "low"]
    sev_part = " ".join(
        f"{name}={by_severity[name]}" for name in sev_order if name in by_severity
    )
    return (
        f"incidents: {len(incidents)} total | {status_part} | "
        f"by_severity: {sev_part} | needs_human={needs_human}"
    )


@dataclass
class IncidentLog:
    """Persistent, auditable append-mostly store of incidents.

    Runtime state (gitignored, like the rest of ``data/*.jsonl``). Mirrors the
    ``ApprovalInbox``/``AlertAckStore`` persistence convention via the
    state-integrity envelope (``read_state_jsonl``/``rewrite_state_jsonl``).
    """

    incidents: list[Incident] = field(default_factory=list)
    path: Path | str | None = None

    def __post_init__(self) -> None:
        if self.path is not None:
            self.path = Path(self.path)
            self.incidents = self._load()

    def open_incident(
        self,
        *,
        severity: IncidentSeverity,
        trigger: str,
        affected_module: str = "",
        containment_action: str = "",
        human_escalation: bool = False,
        postmortem_note: str = "",
    ) -> Incident:
        """Record a new incident and persist it. Returns the stored record."""
        incident = Incident(
            severity=severity,
            trigger=trigger,
            affected_module=affected_module,
            containment_action=containment_action,
            human_escalation=human_escalation,
            postmortem_note=postmortem_note,
        )
        self.incidents.append(incident)
        self._save()
        return incident

    def update(
        self,
        incident_id: str,
        *,
        status: Optional[IncidentStatus] = None,
        containment_action: Optional[str] = None,
        postmortem_note: Optional[str] = None,
        human_escalation: Optional[bool] = None,
    ) -> Incident | None:
        """Update an incident in place. Returns the new record, or None if absent.

        ``resolved`` is only honoured for incidents that do not still owe a
        human (or when the postmortem note is being set in the same call), so a
        critical breakage cannot be silently closed without a note.
        """
        for i, inc in enumerate(self.incidents):
            if inc.id != incident_id:
                continue
            new_status = status if status is not None else inc.status
            if new_status not in _VALID_STATUSES:
                raise ValueError(f"invalid incident status: {new_status}")
            note = postmortem_note if postmortem_note is not None else inc.postmortem_note
            if new_status == "resolved" and inc.severity in _MUST_ESCALATE_SEVERITIES and not str(note).strip():
                raise ValueError(
                    "cannot resolve a critical/high incident without a postmortem_note"
                )
            updated = replace(
                inc,
                status=new_status,
                containment_action=(
                    containment_action if containment_action is not None
                    else inc.containment_action
                ),
                postmortem_note=note,
                human_escalation=(
                    human_escalation if human_escalation is not None
                    else inc.human_escalation
                ),
                updated_at=_now_iso(),
            )
            self.incidents[i] = updated
            self._save()
            return updated
        return None

    def open_incidents(self) -> list[Incident]:
        return [inc for inc in self.incidents if inc.is_open]

    def needing_human(self) -> list[Incident]:
        return [inc for inc in self.incidents if inc.needs_human]

    def summary(self) -> str:
        return summarise_incidents(self.incidents)

    def load(self) -> list[Incident]:
        return list(self.incidents)

    def _load(self) -> list[Incident]:
        assert self.path is not None
        path = Path(self.path)
        if not path.exists():
            return list(self.incidents)
        out: list[Incident] = []
        for raw in read_state_jsonl(path):
            try:
                out.append(Incident.from_dict(raw))
            except (TypeError, ValueError):
                continue
        return out

    def _save(self) -> None:
        if self.path is None:
            return
        rewrite_state_jsonl(Path(self.path), [inc.to_dict() for inc in self.incidents])
