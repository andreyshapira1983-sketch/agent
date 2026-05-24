"""
brain/metrics.py — Phase review KPIs.

The roadmap's "Review" gate requires four metrics that decide whether
the agent advances from one phase to the next:

    intervention_rate
        How often does delivery require a human in the loop?
        Source: AuditLog rows with verdict=REQUIRE_APPROVAL or DENY
        divided by total tool_call rows.

    tokens_per_deliverable
        Total LLM tokens spent / count of DELIVERED jobs.
        Source: BudgetController + JobStore.

    audit_integrity
        Boolean — does AuditLog.verify_chain() pass right now?
        Plus the chain head hash for cross-check.

    lifetime_value (LTV)
        Total dollars earned / count of unique clients. Computed from
        Portfolio's `dollars_spent` aggregated by client_id-prefix (we
        don't have a billing system yet, so this is reuse rate as a
        proxy: jobs_per_client.

The Metrics service is read-only. It accepts the four state-owners as
constructor args and returns a snapshot on demand. Hook it into
StatusServer to expose at `/status/metrics`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .audit import AuditLog
    from .budget import BudgetController
    from .skills.job import JobStore
    from .skills.portfolio import Portfolio

logger = logging.getLogger(__name__)


@dataclass
class MetricsSnapshot:
    """Phase-review dashboard in one structured object."""

    intervention_rate:      float = 0.0       # 0..1
    tokens_per_deliverable: float = 0.0       # average
    audit_integrity_ok:     bool  = True
    audit_chain_head:       str   = ""
    audit_total_entries:    int   = 0
    delivered_jobs:         int   = 0
    failed_jobs:            int   = 0
    declined_jobs:          int   = 0
    active_jobs:            int   = 0
    unique_clients:         int   = 0
    jobs_per_client:        float = 0.0       # LTV proxy
    total_dollars_spent:    float = 0.0       # from Portfolio
    profession_stats:       list  = field(default_factory=list)
    notes:                  list  = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "intervention_rate":      round(self.intervention_rate, 4),
            "tokens_per_deliverable": round(self.tokens_per_deliverable, 1),
            "audit_integrity_ok":     self.audit_integrity_ok,
            "audit_chain_head":       self.audit_chain_head,
            "audit_total_entries":    self.audit_total_entries,
            "delivered_jobs":         self.delivered_jobs,
            "failed_jobs":            self.failed_jobs,
            "declined_jobs":          self.declined_jobs,
            "active_jobs":            self.active_jobs,
            "unique_clients":         self.unique_clients,
            "jobs_per_client":        round(self.jobs_per_client, 2),
            "total_dollars_spent":    round(self.total_dollars_spent, 4),
            "profession_stats":       [s.to_dict() for s in self.profession_stats],
            "notes":                  self.notes,
        }


class MetricsService:
    """Compute the roadmap's phase-review KPIs on demand.

    Construction is free — only the call to `snapshot()` does the heavy
    lifting (a few SQL aggregates and one chain walk). Cache externally
    if you call this often.
    """

    def __init__(
        self,
        *,
        audit: "AuditLog | None" = None,
        budget: "BudgetController | None" = None,
        job_store: "JobStore | None" = None,
        portfolio: "Portfolio | None" = None,
    ) -> None:
        self._audit = audit
        self._budget = budget
        self._jobs = job_store
        self._portfolio = portfolio

    # ────────────────────────────────────────────────────────────────

    def snapshot(self) -> MetricsSnapshot:
        snap = MetricsSnapshot()

        if self._audit is not None:
            self._collect_audit(snap)
        else:
            snap.notes.append("no audit log wired — audit metrics omitted")

        if self._jobs is not None:
            self._collect_jobs(snap)
        else:
            snap.notes.append("no job store wired — job metrics omitted")

        if self._portfolio is not None:
            self._collect_portfolio(snap)
        else:
            snap.notes.append("no portfolio wired — portfolio metrics omitted")

        if self._budget is not None:
            usage = self._budget.session_usage()
            if snap.delivered_jobs > 0:
                snap.tokens_per_deliverable = usage.tokens / snap.delivered_jobs

        return snap

    # ────────────────────────────────────────────────────────────────
    # Sources
    # ────────────────────────────────────────────────────────────────

    def _collect_audit(self, snap: MetricsSnapshot) -> None:
        audit = self._audit
        # Integrity
        report = audit.verify_chain()
        snap.audit_integrity_ok = report.ok
        snap.audit_chain_head = audit.head()
        snap.audit_total_entries = report.total_entries
        if not report.ok:
            snap.notes.append(
                f"AUDIT CHAIN BROKEN at seq={report.first_bad_seq} — investigate immediately"
            )

        # intervention_rate: tool_call rows / tool_call rows that needed approval
        all_calls = 0
        needed_approval = 0
        for entry in audit:
            if entry.action == "tool_call":
                all_calls += 1
                if entry.verdict in {"REQUIRE_APPROVAL", "DENY"}:
                    needed_approval += 1
        if all_calls > 0:
            snap.intervention_rate = needed_approval / all_calls

    def _collect_jobs(self, snap: MetricsSnapshot) -> None:
        from .skills.job import JobStatus
        store = self._jobs
        delivered = store.list_by_status(JobStatus.DELIVERED)
        failed    = store.list_by_status(JobStatus.FAILED)
        declined  = store.list_by_status(JobStatus.DECLINED)
        active    = store.list_active()
        snap.delivered_jobs = len(delivered)
        snap.failed_jobs    = len(failed)
        snap.declined_jobs  = len(declined)
        snap.active_jobs    = len(active)

        # Unique clients = unique client_id values across all jobs
        client_ids = set()
        total_jobs = 0
        for j in store:
            client_ids.add(j.client_id)
            total_jobs += 1
        snap.unique_clients = len(client_ids)
        if client_ids:
            snap.jobs_per_client = total_jobs / len(client_ids)

    def _collect_portfolio(self, snap: MetricsSnapshot) -> None:
        portfolio = self._portfolio
        all_stats = portfolio.all_stats()
        snap.profession_stats = all_stats
        snap.total_dollars_spent = sum(s.dollars_total for s in all_stats)
