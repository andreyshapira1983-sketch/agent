"""Approval inbox for autonomous runtime decisions.

The ordinary ApprovalProvider is synchronous: the loop asks now and waits.
The autonomous runtime needs a second surface: collect items that a human can
review later, while the unattended run stays stopped or dry-run only.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from core.ids import new_id
from core.redaction import redact_payload
from core.state_integrity import read_state_jsonl, rewrite_state_jsonl


ApprovalInboxStatus = Literal["pending", "approved", "denied", "aborted", "executed"]
ApprovalInboxRisk = Literal["read_only", "reversible", "irreversible", "external"]
_VALID_STATUSES = {"pending", "approved", "denied", "aborted", "executed"}
_VALID_RISKS = {"read_only", "reversible", "irreversible", "external"}

# Default TTL for approval requests.  After this many hours without a human
# decision the item is automatically aborted by expire_stale().
# Prevents the inbox from accumulating stale items indefinitely when the
# operator goes offline.
_DEFAULT_TTL_HOURS: int = 24

# Slice 1b-a: map a durable status transition to an explicit receipt operation.
_STATUS_RECEIPT_OPERATION: dict[str, str] = {
    "approved": "approval_inbox.approve",
    "denied": "approval_inbox.deny",
    "aborted": "approval_inbox.abort",
    "executed": "approval_inbox.mark_executed",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_durable_field(value: str) -> str:
    redacted = redact_payload(value)
    return redacted if isinstance(redacted, str) else str(redacted)


def _redact_durable_reasons(
    reasons: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    seq = list(reasons)
    redacted = redact_payload(seq)
    if not isinstance(redacted, list):
        return (str(redacted),)
    return tuple(str(reason) for reason in redacted)


def _redact_durable_payload(payload: dict) -> dict:
    redacted = redact_payload(payload)
    return redacted if isinstance(redacted, dict) else {}


@dataclass(frozen=True)
class ApprovalInboxItem:
    operation: str
    summary: str
    risk: ApprovalInboxRisk = "reversible"
    reasons: tuple[str, ...] = ()
    payload: dict = field(default_factory=dict)
    requested_by: str = "autonomous_runtime"
    expires_at: str | None = None
    id: str = field(default_factory=lambda: new_id("ain"))
    status: ApprovalInboxStatus = "pending"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "operation": self.operation,
            "summary": self.summary,
            "risk": self.risk,
            "reasons": list(self.reasons),
            "payload": self.payload,
            "requested_by": self.requested_by,
            "expires_at": self.expires_at,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
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
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        return cls(
            id=str(data.get("id") or new_id("ain")),
            operation=str(data.get("operation") or ""),
            summary=str(data.get("summary") or ""),
            risk=risk,  # type: ignore[arg-type]
            reasons=tuple(str(reason) for reason in reasons),
            payload=payload,
            requested_by=str(data.get("requested_by") or "autonomous_runtime"),
            expires_at=str(data.get("expires_at")) if data.get("expires_at") else None,
            status=status,  # type: ignore[arg-type]
            created_at=str(data.get("created_at") or _now_iso()),
            updated_at=str(data.get("updated_at") or data.get("created_at") or _now_iso()),
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
        payload: dict | None = None,
        expires_at: str | None = None,
        dedup_key: str | None = None,
    ) -> ApprovalInboxItem:
        # Structural duplicate guard: if a dedup_key is supplied and an
        # equivalent pending item already exists, return it instead of
        # appending a near-identical row. This keeps the inbox drained of the
        # repetitive proposed_task clusters the daemon used to accumulate.
        if dedup_key is not None:
            existing = self._find_pending_by_dedup_key(dedup_key)
            if existing is not None:
                return existing
        if expires_at is None:
            from datetime import timedelta
            expires_at = (
                datetime.now(timezone.utc) + timedelta(hours=_DEFAULT_TTL_HOURS)
            ).isoformat()
        merged_payload = dict(payload or {})
        if dedup_key is not None:
            merged_payload.setdefault("dedup_key", dedup_key)
        safe_summary = _redact_durable_field(summary)
        safe_reasons = _redact_durable_reasons(reasons)
        safe_payload = _redact_durable_payload(merged_payload)
        item = ApprovalInboxItem(
            operation=operation,
            summary=safe_summary,
            risk=risk,
            reasons=safe_reasons,
            payload=safe_payload,
            expires_at=expires_at,
        )
        self.items.append(item)
        self._save()
        self._emit_receipt("approval_inbox.add", item)
        return item

    def _find_pending_by_dedup_key(self, dedup_key: str) -> ApprovalInboxItem | None:
        """Return a still-pending item carrying ``dedup_key``, if any.

        Expired items are first swept by ``pending()`` so a stale duplicate
        never blocks a fresh, legitimately re-proposed task.
        """
        for item in self.pending():
            if item.payload.get("dedup_key") == dedup_key:
                return item
        return None

    def expire_stale(self) -> int:
        """Abort pending items whose ``expires_at`` timestamp has passed.

        Scans ``self.items`` in-place and sets status to ``'aborted'`` for
        every item that is still ``'pending'`` but whose deadline is in the
        past.  Persists immediately if any items were changed.

        Returns the number of items that were aborted.
        """
        now = datetime.now(timezone.utc)
        expired = 0
        new_items: list[ApprovalInboxItem] = []
        for item in self.items:
            if item.status == "pending" and item.expires_at:
                try:
                    exp = datetime.fromisoformat(item.expires_at)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if now > exp:
                        item = replace(item, status="aborted", updated_at=_now_iso())
                        expired += 1
                except (ValueError, TypeError):
                    pass  # malformed timestamp — leave item untouched
            new_items.append(item)
        self.items = new_items
        if expired:
            self._save()
        return expired

    def pending(self) -> list[ApprovalInboxItem]:
        self.expire_stale()  # enforce TTL on every read
        return [item for item in self.items if item.status == "pending"]

    def list(self, *, status: ApprovalInboxStatus | str | None = None) -> list[ApprovalInboxItem]:
        if status in (None, "", "all"):
            return list(self.items)
        return [item for item in self.items if item.status == status]

    def approve(self, item_id: str) -> ApprovalInboxItem:
        return self.set_status(item_id, "approved")

    def deny(self, item_id: str) -> ApprovalInboxItem:
        return self.set_status(item_id, "denied")

    def abort(self, item_id: str) -> ApprovalInboxItem:
        return self.set_status(item_id, "aborted")

    def mark_executed(self, item_id: str) -> ApprovalInboxItem:
        return self.set_status(item_id, "executed")

    def get(self, item_id: str) -> ApprovalInboxItem | None:
        for item in self.items:
            if item.id == item_id:
                return item
        return None

    def set_status(self, item_id: str, status: ApprovalInboxStatus) -> ApprovalInboxItem:
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid approval status: {status}")
        updated: ApprovalInboxItem | None = None
        out: list[ApprovalInboxItem] = []
        for item in self.items:
            if item.id == item_id:
                updated = replace(item, status=status, updated_at=_now_iso())
                out.append(updated)
            else:
                out.append(item)
        if updated is None:
            raise KeyError(f"approval not found: {item_id}")
        self.items = out
        self._save()
        operation = _STATUS_RECEIPT_OPERATION.get(
            status, f"approval_inbox.set_status.{status}"
        )
        self._emit_receipt(operation, updated)
        return updated

    def _receipt_workspace(self) -> Path | None:
        """Resolve the workspace root for the tool-receipt ledger from the inbox path.

        Inbox lives at ``<workspace>/data/approval_inbox.jsonl``; receipts go to
        ``<workspace>/data/tool_receipts.jsonl``. Returns None for in-memory inboxes.
        """
        if self.path is None:
            return None
        p = Path(self.path)
        if p.parent.name == "data":
            return p.parent.parent
        return p.parent

    def _emit_receipt(self, operation: str, item: "ApprovalInboxItem") -> None:
        """Append an approval transition receipt (slice 1b-a). Never raises."""
        try:
            from core.tool_receipts import record_approval_receipt

            record_approval_receipt(
                operation, item, workspace=self._receipt_workspace()
            )
        except Exception:
            pass  # receipts must never break approval-inbox operations

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
        for raw in read_state_jsonl(path):
            try:
                items.append(ApprovalInboxItem.from_dict(raw))
            except (TypeError, ValueError):
                continue
        return items

    def _save(self) -> None:
        if self.path is None:
            return
        path = Path(self.path)
        rewrite_state_jsonl(path, [item.to_dict() for item in self.items])
