"""Approval inbox for autonomous runtime decisions.

The ordinary ApprovalProvider is synchronous: the loop asks now and waits.
The autonomous runtime needs a second surface: collect items that a human can
review later, while the unattended run stays stopped or dry-run only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from core.ids import new_id


ApprovalInboxStatus = Literal["pending", "approved", "denied", "aborted"]
ApprovalInboxRisk = Literal["read_only", "reversible", "irreversible", "external"]
_VALID_STATUSES = {"pending", "approved", "denied", "aborted"}
_VALID_RISKS = {"read_only", "reversible", "irreversible", "external"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ApprovalInboxItem:
    operation: str
    summary: str
    risk: ApprovalInboxRisk = "reversible"
    reasons: tuple[str, ...] = ()
    requested_by: str = "autonomous_runtime"
    id: str = field(default_factory=lambda: new_id("ain"))
    status: ApprovalInboxStatus = "pending"
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "operation": self.operation,
            "summary": self.summary,
            "risk": self.risk,
            "reasons": list(self.reasons),
            "requested_by": self.requested_by,
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ApprovalInboxItem":
        status = str(data.get("status") or "pending")
        risk = str(data.get("risk") or "reversible")
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid approval status: {status}")
        if risk not in _VALID_RISKS:
            raise ValueError(f"invalid approval risk: {risk}")
        reasons = data.get("reasons") or ()
        if not isinstance(reasons, (list, tuple)):
            reasons = (str(reasons),)
        return cls(
            id=str(data.get("id") or new_id("ain")),
            operation=str(data.get("operation") or ""),
            summary=str(data.get("summary") or ""),
            risk=risk,  # type: ignore[arg-type]
            reasons=tuple(str(reason) for reason in reasons),
            requested_by=str(data.get("requested_by") or "autonomous_runtime"),
            status=status,  # type: ignore[arg-type]
            created_at=str(data.get("created_at") or _now_iso()),
        )


@dataclass
class ApprovalInbox:
    items: list[ApprovalInboxItem] = field(default_factory=list)
    path: Path | str | None = None

    def __post_init__(self) -> None:
        if self.path is not None:
            self.path = Path(self.path)
            self.items = self._load()

    def add(
        self,
        *,
        operation: str,
        summary: str,
        risk: ApprovalInboxRisk = "reversible",
        reasons: tuple[str, ...] | list[str] = (),
    ) -> ApprovalInboxItem:
        item = ApprovalInboxItem(
            operation=operation,
            summary=summary,
            risk=risk,
            reasons=tuple(reasons),
        )
        self.items.append(item)
        self._save()
        return item

    def pending(self) -> list[ApprovalInboxItem]:
        return [item for item in self.items if item.status == "pending"]

    def snapshot(self) -> dict:
        pending = self.pending()
        return {
            "total": len(self.items),
            "pending": len(pending),
            "items": [item.to_dict() for item in self.items],
        }

    def _load(self) -> list[ApprovalInboxItem]:
        assert self.path is not None
        path = Path(self.path)
        if not path.exists():
            return list(self.items)
        items: list[ApprovalInboxItem] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    if isinstance(raw, dict):
                        items.append(ApprovalInboxItem.from_dict(raw))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
        return items

    def _save(self) -> None:
        if self.path is None:
            return
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for item in self.items:
                fh.write(json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        tmp.replace(path)
