"""Guard: the historical audit docs redirect status to the single registry.

CORE_AUDIT, OPERATIONAL_FAILURE_MODES, MEMORY_SYSTEM_AUDIT and LIVE_PROBE_FINDINGS
each carry their own (now historical) status ledger. To keep those stale
statuses from being read as current, each must carry a banner redirecting status
ownership to docs/audit/MASTER_ISSUE_REGISTRY.md — the single status owner. This
test fails if that redirect is removed from any of them. Read-only.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

HISTORICAL_AUDIT_DOCS = (
    "docs/CORE_AUDIT_2026-07-18.md",
    "docs/OPERATIONAL_FAILURE_MODES.md",
    "docs/MEMORY_SYSTEM_AUDIT.md",
    "docs/LIVE_PROBE_FINDINGS.md",
)


def test_historical_audit_docs_redirect_status_to_registry():
    missing = []
    for rel in HISTORICAL_AUDIT_DOCS:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        if "MASTER_ISSUE_REGISTRY.md" not in text:
            missing.append(rel)
    assert not missing, (
        "these historical audit docs no longer redirect status to the registry: "
        f"{missing}. Restore the status-ledger-superseded banner pointing at "
        "docs/audit/MASTER_ISSUE_REGISTRY.md."
    )


def test_superseded_banner_present():
    # The redirect must be a visible banner, not an incidental mention.
    missing = []
    for rel in HISTORICAL_AUDIT_DOCS:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8").lower()
        if "superseded" not in text:
            missing.append(rel)
    assert not missing, f"status-ledger-superseded banner missing in: {missing}"
