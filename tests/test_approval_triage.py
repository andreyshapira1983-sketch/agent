from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.approval_inbox import ApprovalInbox, ApprovalInboxItem
from core.approval_triage import (
    TriageReport,
    format_triage_report,
    triage_inbox,
)


_NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)


def _proposal(
    *,
    signature: str,
    summary: str = "do the thing",
    created_at: datetime | None = None,
    risk: str = "reversible",
    rationale: str = "because it matters",
    reasons: tuple[str, ...] = ("rationale",),
    item_id: str | None = None,
) -> ApprovalInboxItem:
    created = (created_at or _NOW).isoformat()
    payload = {"canonical_signature": signature, "rationale": rationale}
    kwargs = {
        "operation": "proposed_task",
        "summary": summary,
        "risk": risk,  # type: ignore[arg-type]
        "reasons": reasons,
        "payload": payload,
        "created_at": created,
        "updated_at": created,
    }
    if item_id is not None:
        kwargs["id"] = item_id
    return ApprovalInboxItem(**kwargs)  # type: ignore[arg-type]


def test_duplicates_group_into_one_cluster_and_keep_only_the_original():
    older = _proposal(
        signature="tests:claim:registry",
        created_at=_NOW - timedelta(hours=2),
        item_id="ain_old",
    )
    newer = _proposal(
        signature="tests:claim:registry",
        created_at=_NOW - timedelta(hours=1),
        item_id="ain_new",
    )

    report = triage_inbox([older, newer], now=_NOW)

    # One cluster of size 2.
    assert len(report.clusters) == 1
    assert report.clusters[0].count == 2
    assert report.clusters[0].signature == "tests:claim:registry"

    # The oldest is kept; the newer is the structural duplicate.
    actions = {i.id: i.recommended_action for i in report.items}
    assert actions["ain_old"] == "keep"
    assert actions["ain_new"] == "dismiss_duplicate"
    assert report.duplicates == ("ain_new",)
    assert report.recommended_dismissals == ("ain_new",)


def test_distinct_signatures_stay_in_separate_clusters():
    a = _proposal(signature="tests:source:registry", item_id="a")
    b = _proposal(signature="learn:claim:distribution", item_id="b")
    c = _proposal(signature="goal:approval:cleanup", item_id="c")

    report = triage_inbox([a, b, c], now=_NOW)

    # Three different signatures => three singleton clusters, no duplicates.
    assert len(report.clusters) == 3
    assert all(cluster.count == 1 for cluster in report.clusters)
    assert report.duplicates == ()
    assert all(i.recommended_action == "keep" for i in report.items)


def test_triage_is_read_only_and_does_not_delete_pending_items(workspace):
    path = workspace / "data" / "approval_inbox.jsonl"
    inbox = ApprovalInbox(path=path)
    inbox.add(
        operation="proposed_task",
        summary="first",
        payload={"canonical_signature": "tests:a", "rationale": "x"},
        dedup_key="proposed_task:tests:a",
    )
    inbox.add(
        operation="proposed_task",
        summary="second",
        payload={"canonical_signature": "tests:b", "rationale": "y"},
        dedup_key="proposed_task:tests:b",
    )

    before = {item.id for item in inbox.pending()}
    report = triage_inbox(inbox.pending(), now=_NOW)
    after = {item.id for item in inbox.pending()}

    # Nothing removed, nothing mutated on disk.
    assert before == after
    assert report.total_pending == 2
    reloaded = ApprovalInbox(path=path)
    assert {item.id for item in reloaded.pending()} == before
    assert all(item.status == "pending" for item in reloaded.list(status="all"))


def test_summary_stays_compact_even_with_many_items():
    items = [
        _proposal(signature=f"tests:topic:{n}", item_id=f"ain_{n}")
        for n in range(28)
    ]

    report = triage_inbox(items, now=_NOW)
    text = format_triage_report(report)

    # Compact one-liner reflects the real total.
    assert "pending=28" in report.compact_summary()
    # The rendered block does not print one row per item: clusters are capped.
    assert text.count("\n") < 20
    assert "+20 more cluster(s)" in text


def test_stale_items_are_flagged_for_review_not_dismissed():
    fresh = _proposal(
        signature="tests:fresh",
        created_at=_NOW - timedelta(hours=1),
        item_id="fresh",
    )
    old = _proposal(
        signature="tests:old",
        created_at=_NOW - timedelta(hours=100),
        item_id="old",
    )

    report = triage_inbox([fresh, old], now=_NOW, stale_after_hours=72)

    assert report.stale == ("old",)
    actions = {i.id: i.recommended_action for i in report.items}
    assert actions["fresh"] == "keep"
    assert actions["old"] == "needs_review"
    # Stale never lands in the dismiss bucket.
    assert "old" not in report.duplicates


def test_dangerous_risk_is_escalated_for_review():
    dangerous = _proposal(
        signature="ops:irreversible",
        risk="irreversible",
        item_id="danger",
    )

    report = triage_inbox([dangerous], now=_NOW)

    assert report.dangerous == ("danger",)
    assert report.items[0].recommended_action == "needs_review"


def test_proposal_without_rationale_is_low_value():
    bare = _proposal(
        signature="tests:bare",
        rationale="",
        reasons=(),
        item_id="bare",
    )

    report = triage_inbox([bare], now=_NOW)

    assert report.low_value == ("bare",)
    assert report.items[0].recommended_action == "needs_review"


def test_dangerous_duplicate_prefers_review_over_dismiss():
    older = _proposal(
        signature="ops:external",
        risk="external",
        created_at=_NOW - timedelta(hours=2),
        item_id="d_old",
    )
    newer = _proposal(
        signature="ops:external",
        risk="external",
        created_at=_NOW - timedelta(hours=1),
        item_id="d_new",
    )

    report = triage_inbox([older, newer], now=_NOW)

    # Dangerous precedence wins: neither is auto-recommended for dismissal.
    assert report.duplicates == ()
    assert all(i.recommended_action == "needs_review" for i in report.items)


def test_clusters_sorted_by_count_descending():
    big = [
        _proposal(signature="tests:big", item_id=f"big_{n}")
        for n in range(3)
    ]
    small = [_proposal(signature="tests:small", item_id="small_0")]

    report = triage_inbox(big + small, now=_NOW)

    assert report.clusters[0].signature == "tests:big"
    assert report.clusters[0].count == 3
    assert report.clusters[1].signature == "tests:small"


def test_non_pending_items_are_ignored():
    pending = _proposal(signature="tests:keep", item_id="p")
    from dataclasses import replace

    executed = replace(
        _proposal(signature="tests:done", item_id="done"),
        status="executed",
    )

    report = triage_inbox([pending, executed], now=_NOW)

    assert report.total_pending == 1
    assert [c.signature for c in report.clusters] == ["tests:keep"]


def test_report_to_dict_is_serialisable_shape():
    report = triage_inbox([_proposal(signature="tests:x", item_id="x")], now=_NOW)
    data = report.to_dict()

    assert data["total_pending"] == 1
    assert data["clusters"][0]["signature"] == "tests:x"
    assert "recommended_dismissals" in data
    assert isinstance(report, TriageReport)
