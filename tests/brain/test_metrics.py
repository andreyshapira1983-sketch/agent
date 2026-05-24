"""Tests for the phase-review Metrics service."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from brain.audit import AuditLog
from brain.budget import BudgetController, BudgetLimits
from brain.metrics import MetricsService
from brain.skills.job import Job, JobStatus, JobStore
from brain.skills.portfolio import Portfolio, PortfolioEntry


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ════════════════════════════════════════════════════════════════════
# Empty / minimal wiring
# ════════════════════════════════════════════════════════════════════

def test_snapshot_with_no_state_returns_zeros() -> None:
    svc = MetricsService()
    snap = svc.snapshot()
    assert snap.intervention_rate == 0.0
    assert snap.tokens_per_deliverable == 0.0
    assert snap.audit_integrity_ok is True
    assert snap.delivered_jobs == 0
    assert snap.unique_clients == 0
    # All three "no X wired" notes should be present
    assert any("audit" in n for n in snap.notes)
    assert any("job store" in n for n in snap.notes)
    assert any("portfolio" in n for n in snap.notes)


def test_to_dict_is_round_trippable_through_json() -> None:
    import json
    svc = MetricsService()
    snap = svc.snapshot()
    payload = snap.to_dict()
    assert isinstance(payload, dict)
    assert "intervention_rate" in payload
    # Must be valid JSON
    encoded = json.dumps(payload)
    assert "intervention_rate" in encoded


# ════════════════════════════════════════════════════════════════════
# Audit-derived metrics
# ════════════════════════════════════════════════════════════════════

def test_audit_metrics_count_intervention_rate(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.db")
    # 3 tool calls — 1 needed approval, 1 denied, 1 ok
    audit.record(actor="tool", action="tool_call", target="email",
                 verdict="OK", params={})
    audit.record(actor="tool", action="tool_call", target="powershell",
                 verdict="REQUIRE_APPROVAL", params={})
    audit.record(actor="tool", action="tool_call", target="file_write",
                 verdict="DENY", params={})
    # Unrelated rows shouldn't count
    audit.record(actor="brain", action="policy_verdict", target="x",
                 verdict="ALLOW", params={})
    audit.record(actor="brain", action="job_status", target="j1",
                 verdict="DELIVERED", params={})

    svc = MetricsService(audit=audit)
    snap = svc.snapshot()

    assert snap.audit_integrity_ok is True
    assert snap.audit_total_entries == 5
    assert pytest.approx(snap.intervention_rate, abs=1e-6) == 2 / 3


def test_audit_metrics_detect_tampered_chain(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.db")
    audit.record(actor="tool", action="tool_call", target="x",
                 verdict="OK", params={})
    # Tamper with the row to force chain mismatch
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    conn.execute("UPDATE audit_entries SET verdict = 'DENY' WHERE seq = 1")
    conn.commit()
    conn.close()

    svc = MetricsService(audit=audit)
    snap = svc.snapshot()
    assert snap.audit_integrity_ok is False
    assert any("AUDIT CHAIN BROKEN" in n for n in snap.notes)


# ════════════════════════════════════════════════════════════════════
# Job-derived metrics
# ════════════════════════════════════════════════════════════════════

def test_job_metrics_count_correctly() -> None:
    store = JobStore(":memory:")

    # 3 jobs — different lifecycles + different clients
    for i, (client, terminal) in enumerate([
        ("alice@example.com", JobStatus.DELIVERED),
        ("alice@example.com", JobStatus.DELIVERED),
        ("bob@example.com",   JobStatus.FAILED),
    ]):
        job = Job(brief=f"job-{i}", source="email", client_id=client)
        store.create(job)
        if terminal == JobStatus.DELIVERED:
            store.update_status(job.id, JobStatus.MATCHED)
            store.update_status(job.id, JobStatus.IN_PROGRESS)
            store.update_status(job.id, JobStatus.DELIVERED)
        else:
            store.update_status(job.id, JobStatus.FAILED)

    # one active job
    j_active = Job(brief="active", source="email", client_id="carol@example.com")
    store.create(j_active)

    # one declined
    j_dec = Job(brief="bad", source="email", client_id="dan@example.com")
    store.create(j_dec)
    store.update_status(j_dec.id, JobStatus.DECLINED)

    svc = MetricsService(job_store=store)
    snap = svc.snapshot()
    assert snap.delivered_jobs == 2
    assert snap.failed_jobs == 1
    assert snap.declined_jobs == 1
    assert snap.active_jobs == 1
    assert snap.unique_clients == 4
    assert pytest.approx(snap.jobs_per_client, abs=1e-6) == 5 / 4


# ════════════════════════════════════════════════════════════════════
# Tokens-per-deliverable
# ════════════════════════════════════════════════════════════════════

def test_tokens_per_deliverable_uses_budget_and_jobs() -> None:
    store = JobStore(":memory:")
    for i in range(4):
        j = Job(brief=f"j{i}", source="email", client_id="x@y.com")
        store.create(j)
        store.update_status(j.id, JobStatus.MATCHED)
        store.update_status(j.id, JobStatus.IN_PROGRESS)
        store.update_status(j.id, JobStatus.DELIVERED)

    budget = BudgetController(per_session=BudgetLimits(tokens=1_000_000))
    budget.record(tokens=2_000)
    budget.record(tokens=2_000)  # session total: 4_000

    svc = MetricsService(budget=budget, job_store=store)
    snap = svc.snapshot()
    assert snap.delivered_jobs == 4
    assert snap.tokens_per_deliverable == 1000.0


def test_zero_delivered_jobs_yields_zero_tokens_per_deliverable() -> None:
    budget = BudgetController()
    budget.record(tokens=5000)
    store = JobStore(":memory:")
    svc = MetricsService(budget=budget, job_store=store)
    snap = svc.snapshot()
    assert snap.tokens_per_deliverable == 0.0


# ════════════════════════════════════════════════════════════════════
# Portfolio-derived metrics
# ════════════════════════════════════════════════════════════════════

def test_portfolio_aggregates_dollars_and_stats(tmp_path: Path) -> None:
    portfolio = Portfolio(tmp_path / "portfolio.db")
    portfolio.record(PortfolioEntry(
        id="auto", profession_id="text_editor", job_id="j1",
        success=True, delivered_at=_iso(),
        tokens_used=1000, dollars_spent=0.05,
    ))
    portfolio.record(PortfolioEntry(
        id="auto", profession_id="translator_en_ru", job_id="j2",
        success=False, delivered_at=_iso(),
        tokens_used=500, dollars_spent=0.02,
    ))

    svc = MetricsService(portfolio=portfolio)
    snap = svc.snapshot()
    assert pytest.approx(snap.total_dollars_spent, abs=1e-6) == 0.07
    prof_ids = {s.profession_id for s in snap.profession_stats}
    assert prof_ids == {"text_editor", "translator_en_ru"}


# ════════════════════════════════════════════════════════════════════
# Full wiring smoke test
# ════════════════════════════════════════════════════════════════════

def test_full_wiring_produces_consistent_snapshot(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.db")
    audit.record(actor="tool", action="tool_call", target="x",
                 verdict="OK", params={})
    audit.record(actor="tool", action="tool_call", target="y",
                 verdict="REQUIRE_APPROVAL", params={})

    budget = BudgetController()
    budget.record(tokens=3_000, dollars=0.03)

    store = JobStore(":memory:")
    j = Job(brief="hi", source="email", client_id="c@d.com")
    store.create(j)
    store.update_status(j.id, JobStatus.MATCHED)
    store.update_status(j.id, JobStatus.IN_PROGRESS)
    store.update_status(j.id, JobStatus.DELIVERED)

    portfolio = Portfolio(tmp_path / "portfolio.db")
    portfolio.record(PortfolioEntry(
        id="auto", profession_id="text_editor", job_id=j.id,
        success=True, delivered_at=_iso(),
        tokens_used=3000, dollars_spent=0.03,
    ))

    svc = MetricsService(
        audit=audit, budget=budget, job_store=store, portfolio=portfolio,
    )
    snap = svc.snapshot()
    assert snap.audit_integrity_ok is True
    assert snap.audit_total_entries == 2
    assert snap.intervention_rate == 0.5
    assert snap.delivered_jobs == 1
    assert snap.tokens_per_deliverable == 3000.0
    assert snap.unique_clients == 1
    assert pytest.approx(snap.total_dollars_spent, abs=1e-6) == 0.03
    assert snap.notes == []  # everything is wired
