"""Tests for TD-038 deterministic backlog target mapping."""
from __future__ import annotations

from pathlib import Path

from core.backlog_target_mapper import (
    MODEL_DISCOVERY_TARGET,
    MappedBacklogCandidate,
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
        "\n".join(
            [
                '"""Live Model Discovery + Provider Catalog diff -- read-only / dry-run (TD-011/012).',
                "It exposes build_discovery_audit and build_discovery_report.",
                "It NEVER writes the catalog.",
                '"""',
                "VALUE = 1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_model_discovery.py").write_text(
        "\n".join(
            [
                '"""Tests for TD-011/012 read-only Live Model Discovery + catalog diff.',
                "The discovery never writes files and has no secret leakage.",
                '"""',
                "from core.model_discovery import build_discovery_audit, build_discovery_report",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_td_011_012_abstract_target_maps_with_local_evidence(workspace: Path) -> None:
    _write_model_discovery_evidence(workspace)

    result = map_backlog_candidate(
        _Candidate(),
        workspace=workspace,
        allowed_targets=("core/redaction.py",),
    )

    assert result.decision == "mapped"
    assert result.ok is True
    assert isinstance(result.candidate, MappedBacklogCandidate)
    assert result.candidate.target_path == MODEL_DISCOVERY_TARGET
    assert result.candidate.source_target_path == "TD-011 / TD-012"
    assert result.candidate.problem_quote == _TITLE
    assert "TECH_DEBT.md:1" in result.candidate.evidence_ref
    assert "core/model_discovery.py:1" in result.candidate.evidence_ref
    assert result.candidate.mapping_rule == "td_011_012_model_discovery"


def test_concrete_allowlisted_candidate_passes_through(workspace: Path) -> None:
    candidate = _Candidate(
        target_path="core/redaction.py",
        problem_quote="grounded concrete target",
        evidence_ref="TECH_DEBT.md:9",
    )

    result = map_backlog_candidate(
        candidate,
        workspace=workspace,
        allowed_targets=("core/redaction.py",),
    )

    assert result.decision == "concrete"
    assert result.candidate is not None
    assert result.candidate.target_path == "core/redaction.py"
    assert result.candidate.problem_quote == "grounded concrete target"


def test_docs_self_build_candidate_passes_through_as_concrete(workspace: Path) -> None:
    candidate = _Candidate(
        target_path="docs/self_build.md",
        problem_quote="- **What:** first grounded target is `docs/self_build.md`",
        evidence_ref="docs/proposals/self-build-grounded-target-coverage-proposal.md:1",
        signal_source="self_build_docs",
    )

    result = map_backlog_candidate(
        candidate,
        workspace=workspace,
        allowed_targets=("docs/self_build.md",),
    )

    assert result.decision == "concrete"
    assert result.ok is True
    assert result.candidate is not None
    assert result.candidate.target_path == "docs/self_build.md"
    assert result.candidate.source_target_path == "docs/self_build.md"
    assert result.candidate.mapping_rule == "concrete"


def test_ambiguous_abstract_target_returns_no_target(workspace: Path) -> None:
    (workspace / "TECH_DEBT.md").write_text(
        "TD-999 \u2014 Vague future work\nStatus: Partial.\n",
        encoding="utf-8",
    )
    candidate = _Candidate(
        target_path="TD-999",
        problem_quote="TD-999 \u2014 Vague future work",
        evidence_ref="TECH_DEBT.md:1",
    )

    result = map_backlog_candidate(
        candidate,
        workspace=workspace,
        allowed_targets=("core/redaction.py",),
    )

    assert result.decision == "no_target"
    assert result.candidate is None
    assert "no deterministic mapper" in result.reason


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


def test_mapper_reads_only_and_creates_no_workspace_files(workspace: Path) -> None:
    _write_model_discovery_evidence(workspace)
    before = {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }

    result = map_backlog_candidate(
        _Candidate(),
        workspace=workspace,
        allowed_targets=("core/redaction.py",),
    )

    after = {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    assert result.decision == "mapped"
    assert after == before


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
