"""Read-only triage for the approval inbox.

Bounded autonomy means the agent *proposes* work and a human approves it. The
risk is that the ``proposed_task`` queue silently grows into administrative
debt: duplicates, stale ideas, and low-value noise nobody ever dismisses.

This module turns that pile into a *managed* surface. It is intentionally
**pure and read-only**:

* it never mutates an :class:`~core.approval_inbox.ApprovalInbox`;
* it never deletes or executes anything;
* it only computes a recommendation per item plus cluster/duplicate views.

The operator decides. ``recommended_action`` is advice, not an action.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal

from core.approval_inbox import ApprovalInboxItem


# The three recommendations the operator sees. Deliberately small: anything
# that is not a clear structural duplicate either stays (``keep``) or is
# escalated for a human look (``needs_review``). We never recommend a blind
# delete.
RecommendedAction = Literal["dismiss_duplicate", "keep", "needs_review"]

# Risk levels that always warrant a human look before any dismissal.
_DANGEROUS_RISKS: frozenset[str] = frozenset({"irreversible", "external"})

# Default: a pending proposal untouched for three days is "stale".
_DEFAULT_STALE_AFTER_HOURS: int = 72


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _signature_of(item: ApprovalInboxItem) -> str:
    """Stable cluster key for a proposed_task item.

    Prefers the canonical signature the runtime stamped into the payload;
    falls back to the dedup_key, then to the operation so non-proposal items
    still cluster sensibly.
    """
    payload = item.payload or {}
    sig = payload.get("canonical_signature")
    if isinstance(sig, str) and sig.strip():
        return sig.strip()
    dedup = payload.get("dedup_key")
    if isinstance(dedup, str) and dedup.strip():
        return dedup.strip()
    return item.operation or "unknown"


def _label_of(signature: str) -> str:
    """Human-readable cluster label derived deterministically from the key.

    ``tests:claim:registry:source`` -> ``claim registry source``. Coarse on
    purpose so the operator can scan clusters at a glance.
    """
    parts = [p for p in signature.split(":") if p]
    if len(parts) <= 1:
        return signature
    return " ".join(parts[1:])


def _is_low_value(item: ApprovalInboxItem) -> bool:
    """A proposal with no justification at all is low-value noise.

    Empty reasons *and* empty rationale: nobody said why it matters. We do not
    judge content quality — only the absence of any stated rationale.
    """
    if item.reasons:
        return False
    payload = item.payload or {}
    rationale = str(payload.get("rationale") or "").strip()
    return not rationale


@dataclass(frozen=True)
class TriageItem:
    id: str
    signature: str
    operation: str
    risk: str
    summary: str
    recommended_action: RecommendedAction
    reason: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "signature": self.signature,
            "operation": self.operation,
            "risk": self.risk,
            "summary": self.summary,
            "recommended_action": self.recommended_action,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TriageCluster:
    signature: str
    label: str
    count: int
    item_ids: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "signature": self.signature,
            "label": self.label,
            "count": self.count,
            "item_ids": list(self.item_ids),
        }


@dataclass(frozen=True)
class TriageReport:
    total_pending: int
    clusters: tuple[TriageCluster, ...] = ()
    items: tuple[TriageItem, ...] = ()
    duplicates: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()
    dangerous: tuple[str, ...] = ()
    low_value: tuple[str, ...] = ()

    @property
    def recommended_dismissals(self) -> tuple[str, ...]:
        """Ids we recommend dismissing — duplicates only, never originals."""
        return self.duplicates

    def to_dict(self) -> dict:
        return {
            "total_pending": self.total_pending,
            "clusters": [c.to_dict() for c in self.clusters],
            "items": [i.to_dict() for i in self.items],
            "duplicates": list(self.duplicates),
            "stale": list(self.stale),
            "dangerous": list(self.dangerous),
            "low_value": list(self.low_value),
            "recommended_dismissals": list(self.recommended_dismissals),
        }

    def compact_summary(self) -> str:
        """One short line: the whole queue health at a glance."""
        return (
            f"pending={self.total_pending} clusters={len(self.clusters)} "
            f"duplicates={len(self.duplicates)} stale={len(self.stale)} "
            f"dangerous={len(self.dangerous)} low_value={len(self.low_value)}"
        )


def triage_inbox(
    items: Iterable[ApprovalInboxItem],
    *,
    now: datetime | None = None,
    stale_after_hours: int = _DEFAULT_STALE_AFTER_HOURS,
) -> TriageReport:
    """Compute a read-only triage view of the pending approval items.

    Pure: ``items`` is read but never mutated, and nothing is written back to
    any inbox. The caller is responsible for passing pending items (typically
    ``inbox.pending()``); non-pending items are ignored defensively.

    Precedence for ``recommended_action`` (first match wins):

    1. dangerous risk -> ``needs_review``
    2. structural duplicate (same signature, not the oldest) -> ``dismiss_duplicate``
    3. stale (older than ``stale_after_hours``) -> ``needs_review``
    4. low-value (no stated rationale) -> ``needs_review``
    5. otherwise -> ``keep``
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    pending = [item for item in items if item.status == "pending"]

    # Group by canonical signature, preserving first-seen order per cluster.
    grouped: dict[str, list[ApprovalInboxItem]] = {}
    for item in pending:
        grouped.setdefault(_signature_of(item), []).append(item)

    # Within each cluster, the oldest (by created_at) is the original we keep;
    # the rest are structural duplicates. Ties fall back to id for determinism.
    duplicate_ids: set[str] = set()
    for members in grouped.values():
        if len(members) < 2:
            continue
        ordered = sorted(
            members,
            key=lambda it: (_parse_iso(it.created_at) or now, it.id),
        )
        for dupe in ordered[1:]:
            duplicate_ids.add(dupe.id)

    stale_cutoff_seconds = max(0, int(stale_after_hours)) * 3600

    triage_items: list[TriageItem] = []
    stale_ids: list[str] = []
    dangerous_ids: list[str] = []
    low_value_ids: list[str] = []
    duplicate_order: list[str] = []

    for item in pending:
        signature = _signature_of(item)
        risk = item.risk
        is_dangerous = risk in _DANGEROUS_RISKS
        is_duplicate = item.id in duplicate_ids

        created = _parse_iso(item.created_at)
        is_stale = (
            created is not None
            and (now - created).total_seconds() > stale_cutoff_seconds
        )
        low_value = _is_low_value(item)

        if is_dangerous:
            dangerous_ids.append(item.id)
        if is_stale:
            stale_ids.append(item.id)
        if low_value:
            low_value_ids.append(item.id)

        if is_dangerous:
            action: RecommendedAction = "needs_review"
            reason = f"dangerous risk={risk}; review before any action"
        elif is_duplicate:
            action = "dismiss_duplicate"
            reason = f"structural duplicate of cluster '{signature}'"
            duplicate_order.append(item.id)
        elif is_stale:
            action = "needs_review"
            reason = f"stale (>{stale_after_hours}h with no decision)"
        elif low_value:
            action = "needs_review"
            reason = "no stated rationale (low-value)"
        else:
            action = "keep"
            reason = "unique, recent, justified"

        triage_items.append(
            TriageItem(
                id=item.id,
                signature=signature,
                operation=item.operation,
                risk=risk,
                summary=(item.summary.splitlines()[0] if item.summary else ""),
                recommended_action=action,
                reason=reason,
            )
        )

    clusters = tuple(
        sorted(
            (
                TriageCluster(
                    signature=sig,
                    label=_label_of(sig),
                    count=len(members),
                    item_ids=tuple(m.id for m in members),
                )
                for sig, members in grouped.items()
            ),
            key=lambda c: (-c.count, c.signature),
        )
    )

    return TriageReport(
        total_pending=len(pending),
        clusters=clusters,
        items=tuple(triage_items),
        duplicates=tuple(duplicate_order),
        stale=tuple(stale_ids),
        dangerous=tuple(dangerous_ids),
        low_value=tuple(low_value_ids),
    )


def format_triage_report(report: TriageReport, *, max_clusters: int = 8) -> str:
    """Render a compact, operator-facing text block. Read-only presentation."""
    lines: list[str] = []
    lines.append(f"approval inbox triage: {report.compact_summary()}")

    if report.clusters:
        lines.append("clusters:")
        for cluster in report.clusters[:max_clusters]:
            lines.append(f"  - {cluster.label} ({cluster.signature}): {cluster.count}")
        remaining = len(report.clusters) - max_clusters
        if remaining > 0:
            lines.append(f"  - ... +{remaining} more cluster(s)")

    if report.duplicates:
        lines.append(f"recommended dismiss (duplicates): {len(report.duplicates)}")
        for item_id in report.duplicates:
            lines.append(f"  - {item_id} -> dismiss_duplicate")

    if report.dangerous:
        lines.append(f"needs review (dangerous): {len(report.dangerous)}")
    if report.stale:
        lines.append(f"needs review (stale): {len(report.stale)}")
    if report.low_value:
        lines.append(f"needs review (low-value): {len(report.low_value)}")

    lines.append("note: triage is read-only; nothing was deleted or executed")
    return "\n".join(lines)
