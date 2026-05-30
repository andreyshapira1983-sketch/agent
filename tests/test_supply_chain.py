from __future__ import annotations

from pathlib import Path

from core.supply_chain import audit_supply_chain


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
      - run: python -m pip install -r requirements.txt
      - run: python -m pip check
      - run: python scripts/audit_release.py
      - run: python -m pytest
      - run: python -m coverage run --branch -m pytest
      - run: python -m coverage report --fail-under=85
""",
        encoding="utf-8",
    )


def test_supply_chain_audit_passes_for_pinned_requirements_and_ci(tmp_path: Path):
    (tmp_path / "requirements.in").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text(
        "pytest==9.0.3\ncoverage==7.14.0\n",
        encoding="utf-8",
    )
    _write_minimal_ci(tmp_path)

    report = audit_supply_chain(tmp_path)

    assert report.ok is True
    assert report.unpinned_requirements == ()
    assert report.missing_ci_gates == ()
    assert report.ci_workflow == ".github/workflows/ci.yml"


def test_supply_chain_audit_flags_range_requirements(tmp_path: Path):
    (tmp_path / "requirements.in").write_text("pytest>=8\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest>=8.0.0\n", encoding="utf-8")
    _write_minimal_ci(tmp_path)

    report = audit_supply_chain(tmp_path)

    assert report.ok is False
    assert report.unpinned_requirements == ("pytest>=8.0.0",)
    assert any("unpinned" in warning for warning in report.warnings)


def test_supply_chain_audit_flags_missing_ci_gates(tmp_path: Path):
    (tmp_path / "requirements.in").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest==9.0.3\n", encoding="utf-8")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "name: ci\n",
        encoding="utf-8",
    )

    report = audit_supply_chain(tmp_path)

    assert report.ok is False
    assert "pip_check" in report.missing_ci_gates
    assert "release_audit" in report.missing_ci_gates
    assert "coverage_branch" in report.missing_ci_gates
    assert "coverage_threshold" in report.missing_ci_gates


def test_supply_chain_summary_mentions_hash_mode_warning(tmp_path: Path):
    (tmp_path / "requirements.in").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest==9.0.3\n", encoding="utf-8")
    _write_minimal_ci(tmp_path)

    summary = audit_supply_chain(tmp_path).user_summary()

    assert "supply-chain audit" in summary
    assert "hash_checking_mode: False" in summary
    assert "hash-checking mode is not enabled yet" in summary
