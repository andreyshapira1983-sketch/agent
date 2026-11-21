"""Tests for TD-038 deterministic backlog target mapping."""
from __future__ import annotations

from pathlib import Path

from core.backlog_target_mapper import (
    map_backlog_candidate,
)

_TITLE = (
    "TD-011 / TD-012 \u2014 Live Model Discovery + Provider Catalog Refresh "
    "(read-only)"
)


class _Candidate:
    def __init__(
        self,
        target_path: str = "TD-011 / TD-012",
        problem_quote: str = _TITLE,
        evidence_ref: str = "TECH_DEBT.md:1",
        signal_source: str = "tech_debt",
    ) -> None:
        self.target_path = target_path
        self.signal_source = signal_source
        self.evidence_ref = evidence_ref
        self.problem_quote = problem_quote
        self.proposed_change = "abstract change"
        self.proof_of_value = "abstract proof"
        self.expected_effect = "abstract effect"
        self.confidence = 0.7


def _write_model_discovery_evidence(workspace: Path, *, title: str = _TITLE) -> None:
    (workspace / "TECH_DEBT.md").write_text(
        f"{title}\nStatus: Partial.\n",
        encoding="utf-8",
    )
    (workspace / "core").mkdir()
    (workspace / "core" / "model_discovery.py").write_text(
        '"""Live Model Discovery + Provider Catalog diff -- read-only / dry-run (TD-011/012).\nIt exposes build_discovery_audit and build_discovery_report.\nIt NEVER writes the catalog.\n"""\nVALUE = 1\n',
        encoding="utf-8",
    )
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_model_discovery.py").write_text(
        '"""Tests for TD-011/012 read-only Live Model Discovery + catalog diff.\nThe discovery never writes files and has no secret leakage.\n"""\nfrom core.model_discovery import build_discovery_audit, build_discovery_report\n',
        encoding="utf-8",
    )


def test_missing_model_discovery_evidence_returns_unknown(workspace: Path) -> None:
    (workspace / "TECH_DEBT.md").write_text(f"{_TITLE}\nStatus: Partial.\n", encoding="utf-8")

    result = map_backlog_candidate(
        _Candidate(),
        workspace=workspace,
        allowed_targets=("core/redaction.py",),
    )

    assert result.decision == "unknown"
    assert result.candidate is None
    assert "missing_evidence" in result.reason


def test_untraceable_model_candidate_returns_no_target(workspace: Path) -> None:
    _write_model_discovery_evidence(workspace, title="TD-999 \u2014 Different item")

    result = map_backlog_candidate(
        _Candidate(),
        workspace=workspace,
        allowed_targets=("core/redaction.py",),
    )

    assert result.decision == "no_target"
    assert result.candidate is None
    assert result.reason == "candidate is not traceable to TECH_DEBT.md"


def test_mapper_has_no_network_or_runtime_imports() -> None:
    import core.backlog_target_mapper as mapper

    src = Path(mapper.__file__).read_text(encoding="utf-8")
    import_lines = [
        line for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    for banned in ("requests", "urllib", "httpx", "socket", "subprocess"):
        assert not any(banned in line for line in import_lines), banned
    for banned in ("approval_inbox", "scheduler", "agent_tick"):
        assert not any(banned in line for line in import_lines), banned
