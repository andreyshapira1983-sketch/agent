"""Approval inbox for autonomous runtime decisions.

The ordinary ApprovalProvider is synchronous: the loop asks now and waits.
The autonomous runtime needs a second surface: collect items that a human can
review later, while the unattended run stays stopped or dry-run only.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from core.ids import new_id
from core.redaction import redact_payload
from core.state_integrity import read_state_jsonl, rewrite_state_jsonl


def _content_id(data: dict) -> str:
    """Постоянное имя строки, у которой своего нет.

    2026-09-21: строке без `id` выдавался `new_id` — новый случайный при
    каждом чтении. `set_status` перечитывает файл и получал уже другое имя:
    «approval not found». Строка висела заявкой, и снять её штатно было
    нельзя. Одна и та же строка — одно и то же имя.
    """
    digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    return f"ain_{digest[:32]}"


ApprovalInboxStatus = Literal["pending", "approved", "denied", "aborted", "executed"]
ApprovalInboxRisk = Literal["read_only", "reversible", "irreversible", "external"]
#: Правка кода полосой самоприменения (core/self_apply_bridge.SELF_APPLY_OPERATION;
#: строкой, чтобы ящик не тянул полосу при импорте).
_SELF_APPLY_OPERATION = "self_apply_lane.run"
_VALID_STATUSES = {"pending", "approved", "denied", "aborted", "executed"}
_VALID_RISKS = {"read_only", "reversible", "irreversible", "external"}

# Canonical location of the approval inbox file, relative to the workspace root.
# Single source of truth: agent_tick and the CLI health/approval commands import
# this instead of re-declaring the path, so it can never drift between callers.
DEFAULT_APPROVAL_INBOX_PATH = Path("data") / "approval_inbox.jsonl"

# Default TTL for approval requests.  After this many hours without a human
# decision the item is automatically aborted by expire_stale().
# Prevents the inbox from accumulating stale items indefinitely when the
# operator goes offline.
_DEFAULT_TTL_HOURS: int = 24

#: How long a DENIAL is remembered by dedup key (block 3, 2026-09-03, audit
#: L10). Measured on the live inbox: the same doctrine draft re-filed three
#: times after denial, the same split step (same digest) twice. Pending-only
#: dedup made every denial a fresh start. Expert default, veto line: the
#: operator may shorten it in one word; the window is the ONLY thing a
#: byte-identical refile has to wait out, a revised one is never blocked.
_DENIAL_MEMORY_HOURS: int = 168

#: Payload keys that describe the refile itself, not the proposal, and are
#: left out of the content fingerprint.
_REFILE_MARKER_KEYS = frozenset({"dedup_key", "revises", "prior_denial_reason"})

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


def _content_fingerprint(summary: str, payload: dict) -> str:
    """What a proposal SAYS, minus the refile markers — equal for a byte-
    identical refile, different for any revision of summary or content."""
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
    #: WHO gave the verdict. `unattributed` is the honest default: assuming the
    #: operator when nobody said so is how a record starts lying comfortably,
    #: and this is the one field whose purpose is to support a claim about the
    #: human (§9). A RECORD of who claimed the verdict, never authentication —
    #: a caller saying "operator" is believed.
    decided_by: str = "unattributed"
    #: The verdict's own words, kept ON the item (block 3, 2026-09-03): a
    #: refile after denial must carry the reason it answers, and the outcomes
    #: journal alone could not be read back by dedup key.
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

    def __post_init__(self) -> None:
        if self.path is not None:
            self.path = Path(self.path)
            self.items = self._load()

    def _sync(self) -> None:
        """Re-read the file before every read or write (block 8, operator's
        word 2026-09-03). A long campaign held the inbox in memory and rewrote
        the file whole on each save, so an operator's verdict written from
        outside was overwritten by the agent's next proposal; the grant and
        the denial of that evening both needed the run stopped. Every mutation
        in this process saves immediately, so disk is the truth and the
        in-memory copy is a cache."""
        if self.path is not None and Path(self.path).exists():
            self.items = self._load()

    def find_pending_by_dedup_key(self, dedup_key: str):
        """Ожидающая заявка с этим ключом, или None.

        Публичный способ спросить о СТОЛКНОВЕНИИ до того, как о нём соврут.
        `add` при совпадении ключа возвращает существующую заявку и ничем не
        отличает её от новой, поэтому вызывающий писал в журнал «предложено» и
        называл длину СВОЕГО текста, тогда как в ящике лежал чужой
        (F-1 в docs/audit/FIELD_CHECK_QUEUE.md).

        Сама дедупликация не оспаривается: она заведена ради дела и
        задокументирована выше. Здесь появляется только возможность узнать
        правду.
        """
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
        reasons = tuple(reasons)
        if dedup_key is not None:
            merged_payload.setdefault("dedup_key", dedup_key)
            # Block 3 (L10): a denial is remembered, not forgotten with the
            # pending row. The SAME content again is refused and the denied
            # item returned (its status says so); a REVISION is admitted and
            # carries the denial it answers, so the reviewer sees a refile.
            # Only a proposal that CARRIES files is remembered: the same
            # bytes again is the same proposal. A permission question
            # (allow_effects, no files) may be asked again after a verdict —
            # MIR-072's contract, kept.
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
        """Return a still-pending item carrying ``dedup_key``, if any.

        Expired items are first swept by ``pending()`` so a stale duplicate
        never blocks a fresh, legitimately re-proposed task.
        """
        for item in self.pending():
            if item.payload.get("dedup_key") == dedup_key:
                return item
        return None

    # ── the commitments view (block 3, 2026-09-03) ───────────────────────
    # The inbox already holds every commitment with its status; what was
    # missing were readers that tell WAITING from EXHAUSTED and DENIED from
    # NEVER SEEN. No second registry: one store, more honest questions.

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
        """Files awaiting a human under pending/approved items.

        ``None`` means «unknown»: some waiting item names no files, so a
        caller must treat every file as waiting (the pre-block-3 behaviour).
        """
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
        """Files whose proposal a human denied within ``hours`` — a cooldown
        set for producers, so a denial sends the hand elsewhere."""
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
        """Abort pending items whose ``expires_at`` timestamp has passed.

        Scans ``self.items`` in-place and sets status to ``'aborted'`` for
        every item that is still ``'pending'`` but whose deadline is in the
        past.  Persists immediately if any items were changed.

        Returns the number of items that were aborted.
        """
        self._sync()
        now = datetime.now(timezone.utc)
        expired = 0
        new_items: list[ApprovalInboxItem] = []
        for item in self.items:
            # Одобренная, но не исполненная правка кода тоже снимается по сроку
            # (план субботы ж, 24.09: заявка разреза step_sanitizer висела
            # «одобренной» бессрочно и ожила, когда код снова совпал с основой).
            # Постоянные разрешения держат свой срок сами (_grant_is_live).
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
        """Refuse an item. The reason is REQUIRED, unlike on `approve`.

        An approval says «yes, as proposed» — its content is the item itself.
        A denial has no content anywhere except the reason: without it the
        record says that something was wrong and never what. Measured
        2026-08-23: work the agent chose for itself is denied at 55–60%
        (`self_build_task.approve` 3 of 5, and not one ever executed), against
        19% for permission to run work a human directed. That refusal rate IS
        the gate nothing automated performs, and the verdict bridge
        (`data/approval_outcomes.jsonl` → `core.charter_goal._recent_verdicts`)
        already carries it back into goal selection. The channel existed and
        could be silently emptied at the one moment it is for.

        Raised BEFORE any status change, so a refused denial leaves the item
        pending rather than half-decided.
        """
        if not str(reason or "").strip():
            raise ValueError(
                "a denial must carry a reason — it is the only content the "
                "refusal has, and the author of the proposal reads it"
            )
        return self._verdict(item_id, "denied", reason, actor)

    def _verdict(
        self, item_id: str, status: str, reason: str, actor: str,
    ) -> ApprovalInboxItem:
        """One path for both verdicts, so the actor cannot be recorded on one
        and forgotten on the other."""
        item = self.set_status(
            item_id, status, decided_by=str(actor).strip() or "unattributed",
            decision_reason=str(reason or ""),
        )
        self._record_outcome(item, status, reason)
        return item

    def _record_outcome(
        self, item: ApprovalInboxItem, verdict: str, reason: str,
    ) -> None:
        """The verdict bridge (2026-08-19): a review outcome becomes state the
        author's next selection can read; a write failure never blocks the
        verdict itself. Lifecycle transitions (executed/aborted) stay out —
        they are plumbing, not review."""
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
                # Block 3 (L10): the outcome names WHAT was judged, so a
                # reader can match a verdict to a key or a file, not only
                # to a summary string.
                "dedup_key": str(item.payload.get("dedup_key") or ""),
                "targets": list(_payload_targets(item.payload))[:8],
            }])
        except Exception:  # noqa: BLE001, S110 — мост не роняет вердикт;
            pass           # недоставленная запись хуже, чем упавший approve? нет

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
        """`decided_by` / `decision_reason` are written only by the verdict
        path. Lifecycle moves (executed/aborted) leave them alone: they are
        plumbing, not review, and stamping an actor on them would attribute a
        verdict nobody gave."""
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid approval status: {status}")
        self._sync()
        updated: ApprovalInboxItem | None = None
        out: list[ApprovalInboxItem] = []
        for item in self.items:
            if item.id == item_id:
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
        """Append an approval transition receipt (slice 1b-a). Never raises."""
        try:
            from core.tool_receipts import record_approval_receipt

            record_approval_receipt(
                operation, item, workspace=self._receipt_workspace()
            )
        except Exception:  # noqa: BLE001, S110 — reason stated above
            pass  # receipts must never break approval-inbox operations

    def digest(self, *, max_pending: int = 20) -> dict:
        """Сводка для ЧЕЛОВЕКА: числа и заголовки, без тел заявок.

        Отдельный вид, а не урезанный `snapshot`: на снимке с целыми записями
        стоит распознавание дубликатов, и обеднить его значило бы выключить
        распознавание. Замер и границы: MIR-167.
        """
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
            # Обрезание объявляется: молчаливое «показано не всё» читается как
            # «это всё», и оператор решает по неполному списку.
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


def close_answered_effects_requests(inbox: ApprovalInbox, grant_id: str) -> list[str]:
    """Закрыть просьбы о разрешении на эффекты, на которые действующий
    стоячий грант уже ответил. Возвращает их идентификаторы.

    2026-09-20/21: грант продлили в 19:14, а шесть просьб `allow_effects`,
    поданных в 19:00–19:10 именно из-за его истечения, висели сутки. Полоса
    самоправки стоит при любой висящей заявке (правило закреплено тестом и не
    меняется), и давно отвеченные просьбы держали её закрытой: 169 циклов,
    ноль работы. Разбор: tests/test_a_grant_answers_the_requests_it_answers.py.

    Статус `aborted`, не `approved`: одобренную просьбу следующий прогон с той
    же целью подобрал бы как одноразовое разрешение. Заявки на правку кода не
    трогаются — их решает человек.
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
