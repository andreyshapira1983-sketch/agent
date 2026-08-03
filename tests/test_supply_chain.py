from __future__ import annotations

import json
from pathlib import Path

from core.supply_chain import (
    audit_supply_chain,
    build_cyclonedx_sbom,
    parse_requirements_lock,
)


HASH_A = "a" * 64
HASH_B = "b" * 64


def _write_minimal_ci(root: Path) -> None:
    workflow = root / ".github" / "workflows"
    workflow.mkdir(parents=True)
    (workflow / "ci.yml").write_text(
        """
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python -m pip install --require-hashes -r requirements.lock
      - run: python -m pip check
      - run: python scripts/generate_sbom.py --check
      - run: python scripts/audit_release.py
      - run: python -m pytest
      - run: python -m coverage run --branch -m pytest
      - run: python -m coverage report --fail-under=85
""",
        encoding="utf-8",
    )


def _write_lock_and_sbom(root: Path) -> None:
    (root / "requirements.lock").write_text(
        f"pytest==9.0.3 --hash=sha256:{HASH_A}\n"
        f"coverage==7.14.0 --hash=sha256:{HASH_B}\n",
        encoding="utf-8",
    )
    payload = build_cyclonedx_sbom(parse_requirements_lock(root / "requirements.lock"))
    (root / "sbom.cdx.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_supply_chain_audit_passes_for_hash_lock_sbom_and_ci(tmp_path: Path):
    (tmp_path / "requirements.in").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text(
        "pytest==9.0.3\ncoverage==7.14.0\n",
        encoding="utf-8",
    )
    _write_lock_and_sbom(tmp_path)
    _write_minimal_ci(tmp_path)

    report = audit_supply_chain(tmp_path)

    assert report.ok is True
    assert report.unpinned_requirements == ()
    assert report.unhashed_lock_entries == ()
    assert report.hash_checking_mode is True
    assert report.sbom_matches_lock is True
    assert report.sbom_components == 2
    assert report.missing_ci_gates == ()
    assert report.ci_workflow == ".github/workflows/ci.yml"


def test_supply_chain_audit_flags_range_requirements(tmp_path: Path):
    (tmp_path / "requirements.in").write_text("pytest>=8\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest>=8.0.0\n", encoding="utf-8")
    _write_lock_and_sbom(tmp_path)
    _write_minimal_ci(tmp_path)

    report = audit_supply_chain(tmp_path)

    assert report.ok is False
    assert report.unpinned_requirements == ("pytest>=8.0.0",)
    assert any("unpinned" in warning for warning in report.warnings)


def test_supply_chain_audit_flags_missing_ci_gates(tmp_path: Path):
    (tmp_path / "requirements.in").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest==9.0.3\n", encoding="utf-8")
    _write_lock_and_sbom(tmp_path)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "name: ci\n",
        encoding="utf-8",
    )

    report = audit_supply_chain(tmp_path)

    assert report.ok is False
    assert "pip_install_hash_lock" in report.missing_ci_gates
    assert "release_audit" in report.missing_ci_gates
    assert "sbom_check" in report.missing_ci_gates
    assert "pip_check" in report.missing_ci_gates
    assert "coverage_branch" in report.missing_ci_gates
    assert "coverage_threshold" in report.missing_ci_gates


def test_supply_chain_audit_flags_unhashed_lock_entries(tmp_path: Path):
    (tmp_path / "requirements.in").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest==9.0.3\n", encoding="utf-8")
    (tmp_path / "requirements.lock").write_text("pytest==9.0.3\n", encoding="utf-8")
    (tmp_path / "sbom.cdx.json").write_text("{}", encoding="utf-8")
    _write_minimal_ci(tmp_path)

    report = audit_supply_chain(tmp_path)

    assert report.ok is False
    assert report.unhashed_lock_entries == ("pytest==9.0.3",)
    assert any("without sha256 hashes" in warning for warning in report.warnings)


def test_supply_chain_summary_reports_lock_and_sbom_state(tmp_path: Path):
    (tmp_path / "requirements.in").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest==9.0.3\n", encoding="utf-8")
    _write_lock_and_sbom(tmp_path)
    _write_minimal_ci(tmp_path)

    summary = audit_supply_chain(tmp_path).user_summary()

    assert "supply-chain audit" in summary
    assert "hash_checking_mode: True" in summary
    assert "lock: packages=2 unhashed=0" in summary
    assert "sbom: components=2 matches_lock=True" in summary


def test_supply_chain_audit_flags_stale_sbom(tmp_path: Path):
    (tmp_path / "requirements.in").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest==9.0.3\n", encoding="utf-8")
    (tmp_path / "requirements.lock").write_text(
        f"pytest==9.0.3 --hash=sha256:{HASH_A}\n",
        encoding="utf-8",
    )
    (tmp_path / "sbom.cdx.json").write_text(
        json.dumps({"bomFormat": "CycloneDX", "components": []}),
        encoding="utf-8",
    )
    _write_minimal_ci(tmp_path)

    report = audit_supply_chain(tmp_path)

    assert report.ok is False
    assert report.sbom_matches_lock is False
    assert any("out of sync" in warning for warning in report.warnings)
