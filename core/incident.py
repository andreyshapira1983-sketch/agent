"""Incident handling skeleton: a structured incident record plus a small append-only store.

    Pure record and thin persistence: no LLM, no network, no auto-remediation.
    Incidents are opened, then status/postmortem are updated in place; history is
    never silently deleted. Persisted as gitignored runtime state through the
    state-integrity envelope."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

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
    """A single structured incident record.

    ``human_escalation`` is forced True for critical/high severity so a real
    breakage can never be auto-closed."""

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
    def from_dict(cls, data: dict) -> Incident:
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
    """One-line operator digest. Pure, no I/O."""
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
    """Persistent append-mostly store of incidents, via the state-integrity envelope."""

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
        status: IncidentStatus | None = None,
        containment_action: str | None = None,
        postmortem_note: str | None = None,
        human_escalation: bool | None = None,
    ) -> Incident | None:
        """Update an incident in place. Returns the new record, or None if absent.

        ``resolved`` is honoured only for incidents that no longer owe a human, or
        when the postmortem note is set in the same call."""
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

    def status_line(self) -> str:
        """One line for the daemon status, or "" when nothing is open."""
        opened = self.open_incidents()
        if not opened:
            return ""
        owed = self.needing_human()
        newest = max(opened, key=lambda inc: inc.updated_at)
        return (
            f"Incidents: {len(opened)} open, {len(owed)} awaiting a human — "
            f"latest {newest.trigger} ({newest.severity}, {newest.updated_at[:19]})."
        )

    def load(self) -> list[Incident]:
        return list(self.incidents)

    def _load(self) -> list[Incident]:
        assert self.path is not None  # noqa: S101 — type narrowing, guarded above
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
