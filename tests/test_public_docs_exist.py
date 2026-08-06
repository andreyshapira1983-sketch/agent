"""Guard: the public/operator documentation layer exists and is wired.

Phase 1 of the documentation reconciliation added a consolidated public layer
(license, security, contributing, changelog, operations, configuration). This
test fails loudly if one of those files is deleted or emptied, or if the two
consolidated docs stop being routed from docs/INDEX.md. Read-only.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# path -> minimum sensible length, so an emptied file is caught too.
# SECURITY/CONTRIBUTING/CHANGELOG dropped 2026-08-05: the operator deleted
# them as unused documentation, so guarding them would pin what is gone.
REQUIRED = {
    "LICENSE": 200,
    "docs/OPERATIONS.md": 800,
    "docs/CONFIGURATION.md": 800,
}


def test_public_docs_exist_and_are_nontrivial():
    missing = []
    for rel, min_len in REQUIRED.items():
        path = REPO_ROOT / rel
        if not path.is_file() or len(path.read_text(encoding="utf-8")) < min_len:
            missing.append(rel)
    assert not missing, f"public docs missing or too short: {missing}"


def test_index_routes_to_new_docs():
    index = (REPO_ROOT / "docs" / "INDEX.md").read_text(encoding="utf-8")
    assert "OPERATIONS.md" in index
    assert "CONFIGURATION.md" in index



def test_license_is_proprietary_not_osi():
    lic = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8").lower()
    assert "all rights reserved" in lic
    # guard against a silent swap to an OSI licence
    assert "mit license" not in lic
    assert "apache license" not in lic
    assert "gnu general public license" not in lic
