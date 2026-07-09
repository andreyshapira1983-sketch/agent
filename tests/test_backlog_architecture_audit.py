"""Tests for wiring the architecture audit into the grounded backlog (PR #5).

The agent's own ``core.architecture_audit`` finds structural gaps in the repo.
These tests prove those gaps become grounded, traceable backlog candidates so
the self-build loop can discover its own work from self-analysis instead of only
from human-authored docs.
"""
from __future__ import annotations

from pathlib import Path

from core.architecture_audit import audit_architecture
from core.backlog_selector import build_backlog, load_backlog, select_top
from core.backlog_signals import (
    ARCHITECTURE_AUDIT_SOURCE,
    architecture_audit_candidates,
)


_GAPS = [
    {
        "id": "doctrine_and_architecture_docs",
        "title": "Doctrine and Architecture Source of Truth",
        "status": "gap",
        "evidence_files": ["AGENT_DOCTRINE.md", "README.md"],
    },
    {
        "id": "release_hygiene_guard",
        "title": "Release / Supply-Chain Guard",
        "status": "gap",
        "evidence_files": ["core/release_hygiene.py", "sbom.cdx.json"],
    },
]


# ── pure candidate creation ───────────────────────────────────────────────────


def test_gaps_become_traceable_signals():
    records, source_text = architecture_audit_candidates(_GAPS)

    assert len(records) == 2
    first = records[0]
    assert first.signal_source == ARCHITECTURE_AUDIT_SOURCE
    assert first.target_path == "AGENT_DOCTRINE.md"  # first evidence file
    assert first.problem_quote == "Doctrine and Architecture Source of Truth"
    assert first.evidence_ref == "architecture_audit:doctrine_and_architecture_docs"
    # Every quote is verbatim inside the returned source text -> provenance holds.
    for rec in records:
        assert rec.problem_quote in source_text


def test_gap_without_evidence_uses_synthetic_target():
    records, _ = architecture_audit_candidates(
        [{"id": "x", "title": "Some Missing Layer", "evidence_files": []}]
    )
    assert records[0].target_path == "architecture:some-missing-layer"


def test_empty_and_titleless_gaps_yield_nothing():
    assert architecture_audit_candidates([]) == ([], "")
    records, text = architecture_audit_candidates([{"id": "x", "title": "  "}])
    assert records == []
    assert text == ""


def test_duplicate_gap_ids_deduped():
    records, _ = architecture_audit_candidates([_GAPS[0], _GAPS[0]])
    assert len(records) == 1


# ── build_backlog integration + ranking ───────────────────────────────────────


_TECH_DEBT = "TD-060 — Open A\nСтатус: Partial — not finished.\n"
_ANATOMY = (
    "## Candidate follow-ups (TD-030+) — advisory only\n\n"
    "1. **TD-030 (candidate): Do the thing.** rationale.\n"
)


def test_build_backlog_includes_audit_candidates():
    records, text = architecture_audit_candidates(_GAPS)
    backlog = build_backlog(
        architecture_audit_records=records,
        architecture_audit_text=text,
    )
    sources = {c.signal_source for c in backlog}
    assert ARCHITECTURE_AUDIT_SOURCE in sources
    assert len(backlog) == 2


def test_audit_ranks_below_tech_debt_and_docs_above_anatomy():
    records, text = architecture_audit_candidates(_GAPS)
    backlog = build_backlog(
        tech_debt_text=_TECH_DEBT,
        anatomy_text=_ANATOMY,
        architecture_audit_records=records,
        architecture_audit_text=text,
    )
    order = [c.signal_source for c in backlog]
    assert order[0] == "tech_debt"  # base 2.0
    assert order[-1] == "anatomy"   # base 1.0
    # architecture_audit (1.25) sits above anatomy but below tech_debt.
    audit_idx = order.index(ARCHITECTURE_AUDIT_SOURCE)
    assert order.index("anatomy") > audit_idx
    assert order.index("tech_debt") < audit_idx


def test_untraceable_audit_quote_is_dropped():
    # A quote not present in the source text fails provenance and is dropped.
    records, _ = architecture_audit_candidates(_GAPS)
    backlog = build_backlog(
        architecture_audit_records=records,
        architecture_audit_text="totally unrelated text",
    )
    assert backlog == []


# ── end-to-end against the real repo ──────────────────────────────────────────


_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_load_backlog_surfaces_real_audit_gaps():
    audit = audit_architecture(_REPO_ROOT)
    backlog = load_backlog(_REPO_ROOT)
    audit_candidates = [
        c for c in backlog if c.signal_source == ARCHITECTURE_AUDIT_SOURCE
    ]
    if audit.priority_gaps:
        # Every real priority gap the agent finds should surface as a candidate.
        assert audit_candidates
        gap_titles = {gap.title for gap in audit.priority_gaps}
        assert {c.problem_quote for c in audit_candidates} <= gap_titles
    else:  # pragma: no cover - repo currently has gaps
        assert audit_candidates == []


def test_load_backlog_skips_audit_for_non_repo_workspace(tmp_path: Path):
    # Without the sentinel (core/architecture_audit.py) no audit runs, so an
    # unrelated workspace never gets spurious "missing file" gaps.
    (tmp_path / "TECH_DEBT.md").write_text(_TECH_DEBT, encoding="utf-8")
    backlog = load_backlog(tmp_path)
    assert not any(
        c.signal_source == ARCHITECTURE_AUDIT_SOURCE for c in backlog
    )
    assert any(c.signal_source == "tech_debt" for c in backlog)
