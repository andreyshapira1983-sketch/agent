"""Approval inbox: items a human reviews later while the unattended run stays stopped or dry-run."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from core.ids import new_id
from core.redaction import redact_payload
from core.state_integrity import read_state_jsonl, rewrite_state_jsonl


def _content_id(data: dict) -> str:
    """Stable id for a row that has none (a random `new_id` per read made `set_status` fail)."""
    digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    return f"ain_{digest[:32]}"


ApprovalInboxStatus = Literal["pending", "approved", "denied", "aborted", "executed"]
ApprovalInboxRisk = Literal["read_only", "reversible", "irreversible", "external"]
#: Code edit by the self-apply lane (core/self_apply_bridge.SELF_APPLY_OPERATION), a string so the inbox does not import the lane.
_SELF_APPLY_OPERATION = "self_apply_lane.run"
logger = logging.getLogger(__name__)

_VALID_STATUSES = {"pending", "approved", "denied", "aborted", "executed"}
_VALID_RISKS = {"read_only", "reversible", "irreversible", "external"}

#: The only legal moves. A status missing from the keys is terminal: a verdict or an execution is a record, and a record that can be rewritten is not one.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"approved", "denied", "aborted"}),
    "approved": frozenset({"executed", "aborted"}),
}


class ApprovalTransitionError(ValueError):
    """A status move the approval state machine does not allow."""

# Canonical inbox file path relative to the workspace root; import it instead of re-declaring.
DEFAULT_APPROVAL_INBOX_PATH = Path("data") / "approval_inbox.jsonl"

# Default TTL in hours; stale pending items are aborted by expire_stale().
_DEFAULT_TTL_HOURS: int = 24

#: How long a DENIAL is remembered by dedup key; without it a pending-only dedup made every denial a fresh start. The window is the ONLY thing a byte-identical refile waits out; a revised one is never blocked.
_DENIAL_MEMORY_HOURS: int = 168

#: Payload keys that describe the refile itself, not the proposal; left out of the content fingerprint.
_REFILE_MARKER_KEYS = frozenset({"dedup_key", "revises", "prior_denial_reason"})

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


def _content_fingerprint(summary: str, payload: dict) -> str:
    """What a proposal says, minus refile markers: equal for a byte-identical refile, different for a revision."""
    import hashlib
    import json

    body = {k: v for k, v in (payload or {}).items() if k not in _REFILE_MARKER_KEYS}
    raw = json.dumps([str(summary or ""), body], sort_keys=True, ensure_ascii=False,
                     default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _within_hours(ts: str, hours: int) -> bool:
    """True when ``ts`` is newer than ``hours`` ago; unparseable = not recent."""
    from datetime import timedelta

    try:
        when = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - when <= timedelta(hours=hours)


def _payload_targets(payload: dict) -> tuple[str, ...]:
    """File paths a proposal touches (`payload.files[*].path`), else empty."""
    files = (payload or {}).get("files")
    if not isinstance(files, list):
        return ()
    out: list[str] = []
    for entry in files:
        path = str((entry or {}).get("path") or "").replace("\\", "/").strip() \
            if isinstance(entry, dict) else ""
        if path:
            out.append(path)
    return tuple(out)


@dataclass(frozen=True)
class ApprovalInboxItem:
    operation: str
    summary: str
    risk: ApprovalInboxRisk = "reversible"
    reasons: tuple[str, ...] = ()
    payload: dict = field(default_factory=dict)
    requested_by: str = "autonomous_runtime"
    #: WHO gave the verdict. `unattributed` is the honest default. A RECORD of who claimed the verdict, never authentication: a caller saying "operator" is believed.
    decided_by: str = "unattributed"
    #: The verdict's own words, kept ON the item: a refile after denial must carry the reason it answers.
    decision_reason: str = ""
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
            "decided_by": self.decided_by,
            "decision_reason": self.decision_reason,
            "expires_at": self.expires_at,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ApprovalInboxItem:
        status = data.get("status", "pending")
        risk = str(data.get("risk") or "reversible")
        if not isinstance(status, str) or status not in _VALID_STATUSES:
            raise ValueError(f"invalid approval status: {status!r}")
        if risk not in _VALID_RISKS:
            raise ValueError(f"invalid approval risk: {risk}")
        reasons = data.get("reasons") or ()
        if not isinstance(reasons, (list, tuple)):
            reasons = (str(reasons),)
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        return cls(
            id=str(data.get("id") or _content_id(data)),
            operation=str(data.get("operation") or ""),
            summary=str(data.get("summary") or ""),
            risk=risk,  # type: ignore[arg-type]
            reasons=tuple(str(reason) for reason in reasons),
            payload=payload,
            requested_by=str(data.get("requested_by") or "autonomous_runtime"),
            decided_by=str(data.get("decided_by") or "unattributed"),
            decision_reason=str(data.get("decision_reason") or ""),
            expires_at=str(data.get("expires_at")) if data.get("expires_at") else None,
            status=status,  # type: ignore[arg-type]
            created_at=str(data.get("created_at") or _now_iso()),
            updated_at=str(data.get("updated_at") or data.get("created_at") or _now_iso()),
        )


@dataclass
class ApprovalInbox:
    items: list[ApprovalInboxItem] = field(default_factory=list)
    path: Path | str | None = None
    #: Rows of the file that are not a valid item: kept on disk, never actionable.
    unreadable_rows: list[dict] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.path is not None:
            self.path = Path(self.path)
            self.items = self._load()

    def _sync(self) -> None:
        """Re-read the file before every read or write; disk is the truth, memory is a cache."""
        if self.path is not None and Path(self.path).exists():
            self.items = self._load()

    def find_pending_by_dedup_key(self, dedup_key: str):
        """Pending item with this dedup key, or None."""
        return self._find_pending_by_dedup_key(dedup_key)

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
        self._sync()
        # Structural duplicate guard: return an equivalent pending item instead of appending a near-copy.
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
        reasons = tuple(reasons)
        if dedup_key is not None:
            merged_payload.setdefault("dedup_key", dedup_key)
            # A denial is remembered: the same content is refused, a revision is admitted and carries the denial it answers.
            # Only proposals with files are remembered; a permission question (allow_effects, no files) may be asked again.
            denied = (
                self.recently_denied(dedup_key)
                if _payload_targets(merged_payload) else None
            )
            if denied is not None:
                if _content_fingerprint(summary, merged_payload) == \
                        _content_fingerprint(denied.summary, denied.payload):
                    self._emit_receipt("approval_inbox.refuse_repeat", denied)
                    return denied
                merged_payload["revises"] = denied.id
                merged_payload["prior_denial_reason"] = denied.decision_reason
                why = denied.decision_reason or "(no reason recorded)"
                reasons = (f"revises denied {denied.id}: {why}", *reasons)
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
        """Still-pending item carrying ``dedup_key``, if any; expired items are swept first."""
        for item in self.pending():
            if item.payload.get("dedup_key") == dedup_key:
                return item
        return None


    def find_by_dedup_key(
        self, dedup_key: str, *, statuses: tuple[str, ...] = ("pending",),
    ) -> ApprovalInboxItem | None:
        """Newest item with this key in one of ``statuses``, or None."""
        self._sync()
        found = None
        for item in self.items:
            if item.status in statuses and item.payload.get("dedup_key") == dedup_key:
                found = item
        return found

    def recently_denied(
        self, dedup_key: str, *, hours: int = _DENIAL_MEMORY_HOURS,
    ) -> ApprovalInboxItem | None:
        """The denial this key still answers to, or None once it has aged out."""
        item = self.find_by_dedup_key(dedup_key, statuses=("denied",))
        if item is None or not _within_hours(item.updated_at, hours):
            return None
        return item

    def pending_targets(
        self, *, operation: str | None = None,
    ) -> frozenset[str] | None:
        """Files awaiting a human under pending/approved items; ``None`` means some waiting item names no files, so treat every file as waiting."""
        self._sync()
        out: set[str] = set()
        for item in self.items:
            if item.status not in ("pending", "approved"):
                continue
            if operation is not None and item.operation != operation:
                continue
            paths = _payload_targets(item.payload)
            if not paths:
                return None
            out.update(paths)
        return frozenset(out)

    def recently_denied_targets(
        self, *, operation: str | None = None, hours: int = _DENIAL_MEMORY_HOURS,
    ) -> frozenset[str]:
        """Files whose proposal a human denied within ``hours`` (cooldown set for producers)."""
        self._sync()
        out: set[str] = set()
        for item in self.items:
            if item.status != "denied" or not _within_hours(item.updated_at, hours):
                continue
            if operation is not None and item.operation != operation:
                continue
            out.update(_payload_targets(item.payload))
        return frozenset(out)

    def expire_stale(self) -> int:
        """Abort pending items whose ``expires_at`` has passed; persist and return the number aborted."""
        self._sync()
        now = datetime.now(timezone.utc)
        expired = 0
        new_items: list[ApprovalInboxItem] = []
        for item in self.items:
            # Approved but unexecuted code edits also expire; standing grants keep their own TTL (_grant_is_live).
            stale_patch = item.status == "approved" and item.operation == _SELF_APPLY_OPERATION
            if (item.status == "pending" or stale_patch) and item.expires_at:
                try:
                    exp = datetime.fromisoformat(item.expires_at)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if now > exp:
                        item = replace(item, status="aborted", updated_at=_now_iso())
                        expired += 1
                except (ValueError, TypeError):
                    pass
            new_items.append(item)
        self.items = new_items
        if expired:
            self._save()
        return expired

    def pending(self) -> list[ApprovalInboxItem]:
        self.expire_stale()
        return [item for item in self.items if item.status == "pending"]

    def list(self, *, status: ApprovalInboxStatus | str | None = None) -> list[ApprovalInboxItem]:
        self._sync()
        if status in (None, "", "all"):
            return list(self.items)
        return [item for item in self.items if item.status == status]

    def approve(
        self, item_id: str, *, reason: str = "", actor: str = "",
    ) -> ApprovalInboxItem:
        return self._verdict(item_id, "approved", reason, actor)

    def deny(
        self, item_id: str, *, reason: str = "", actor: str = "",
    ) -> ApprovalInboxItem:
        """Refuse an item. The reason is REQUIRED; raised before any status change so a refused denial leaves the item pending."""
        if not str(reason or "").strip():
            raise ValueError(
                "a denial must carry a reason — it is the only content the "
                "refusal has, and the author of the proposal reads it"
            )
        return self._verdict(item_id, "denied", reason, actor)

    def _verdict(
        self, item_id: str, status: str, reason: str, actor: str,
    ) -> ApprovalInboxItem:
        """One path for both verdicts so the actor is recorded on both."""
        prior = self.get(item_id)
        item = self.set_status(
            item_id, status, decided_by=str(actor).strip() or "unattributed",
            decision_reason=str(reason or ""),
        )
        # A repeated verdict changed nothing, so it is not a second outcome.
        if prior is None or prior.status != item.status:
            self._record_outcome(item, status, reason)
        return item

    def _record_outcome(
        self, item: ApprovalInboxItem, verdict: str, reason: str,
    ) -> None:
        """Turn a review outcome into state the next goal selection can read; a write failure never blocks the verdict. Lifecycle moves stay out."""
        ws = self._receipt_workspace()
        if ws is None:
            return
        try:
            from datetime import datetime, timezone

            from core.state_integrity import append_state_jsonl

            append_state_jsonl(ws / "data" / "approval_outcomes.jsonl", [{
                "ts": datetime.now(timezone.utc).isoformat(),
                "id": item.id,
                "operation": item.operation,
                "summary": item.summary,
                "verdict": verdict,
                "reason": str(reason or ""),
                "decided_by": item.decided_by,
                # The outcome names what was judged (key or file), not only a summary string.
                "dedup_key": str(item.payload.get("dedup_key") or ""),
                "targets": list(_payload_targets(item.payload))[:8],
            }])
        except Exception:  # noqa: BLE001, S110 — the bridge must not drop the verdict
            pass

    def abort(self, item_id: str) -> ApprovalInboxItem:
        return self.set_status(item_id, "aborted")

    def mark_executed(self, item_id: str) -> ApprovalInboxItem:
        return self.set_status(item_id, "executed")

    def get(self, item_id: str) -> ApprovalInboxItem | None:
        self._sync()
        for item in self.items:
            if item.id == item_id:
                return item
        return None

    def set_status(
        self,
        item_id: str,
        status: ApprovalInboxStatus,
        *,
        decided_by: str | None = None,
        decision_reason: str | None = None,
    ) -> ApprovalInboxItem:
        """`decided_by` / `decision_reason` are written only by the verdict path, not by lifecycle moves."""
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid approval status: {status}")
        self._sync()
        updated: ApprovalInboxItem | None = None
        out: list[ApprovalInboxItem] = []
        for item in self.items:
            if item.id == item_id:
                # A retry repeating a live status changes nothing: the first signature stands.
                if status == item.status and status in _ALLOWED_TRANSITIONS:
                    return item
                if status not in _ALLOWED_TRANSITIONS.get(item.status, frozenset()):
                    raise ApprovalTransitionError(
                        f"approval {item_id} is {item.status}; "
                        f"it cannot become {status}"
                    )
                updated = replace(
                    item, status=status, updated_at=_now_iso(),
                    **({"decided_by": decided_by} if decided_by else {}),
                    **({"decision_reason": decision_reason}
                       if decision_reason is not None else {}),
                )
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

    def _emit_receipt(self, operation: str, item: ApprovalInboxItem) -> None:
        """Append an approval transition receipt. Never raises."""
        try:
            from core.tool_receipts import record_approval_receipt

            record_approval_receipt(
                operation, item, workspace=self._receipt_workspace()
            )
        except Exception:  # noqa: BLE001, S110 — reason stated above
            pass  # receipts must never break inbox operations

    def digest(self, *, max_pending: int = 20) -> dict:
        """Human-facing summary: counts and titles, no bodies."""
        pending = self.pending()
        by_operation: dict[str, int] = {}
        for item in pending:
            by_operation[item.operation] = by_operation.get(item.operation, 0) + 1
        return {
            "total": len(self.items),
            "pending": len(pending),
            "pending_by_operation": by_operation,
            "pending_items": [
                {
                    "id": item.id,
                    "operation": item.operation,
                    "risk": item.risk,
                    "expires_at": item.expires_at,
                    "summary": item.summary[:160],
                }
                for item in pending[:max_pending]
            ],
            # Truncation is announced so a partial list is not read as complete.
            "pending_not_listed": max(0, len(pending) - max_pending),
        }

    def snapshot(self) -> dict:
        pending = self.pending()
        return {
            "total": len(self.items),
            "pending": len(pending),
            "items": [item.to_dict() for item in self.items],
        }

    def _load(self) -> list[ApprovalInboxItem]:
        assert self.path is not None  # noqa: S101 — type narrowing, guarded above
        path = Path(self.path)
        if not path.exists():
            return list(self.items)
        items: list[ApprovalInboxItem] = []
        unreadable: list[dict] = []
        for raw in read_state_jsonl(path):
            try:
                items.append(ApprovalInboxItem.from_dict(raw))
            except (TypeError, ValueError) as exc:
                unreadable.append(raw)
                if raw not in self.unreadable_rows:
                    logger.warning(
                        "approval inbox row kept in %s but not actionable (%s): id=%s",
                        path, exc, raw.get("id", "?"),
                    )
        self.unreadable_rows = unreadable
        return items

    def _save(self) -> None:
        if self.path is None:
            return
        path = Path(self.path)
        rewrite_state_jsonl(path, [item.to_dict() for item in self.items] + self.unreadable_rows)


def close_answered_effects_requests(inbox: ApprovalInbox, grant_id: str) -> list[str]:
    """Close allow_effects requests already answered by a live standing grant (status `aborted`, not `approved`); return their ids.
    Code-edit requests are left to the human.
    """
    closed: list[str] = []
    for item in inbox.pending():
        if item.operation != "autonomous_runtime.allow_effects":
            continue
        inbox.set_status(
            item.id, "aborted", decided_by=f"standing_grant:{grant_id}",
            decision_reason=(
                f"отвечено действующим стоячим грантом {grant_id}: разрешение "
                "на эффекты есть, просьба о нём больше не нужна"
            ),
        )
        closed.append(item.id)
    return closed
