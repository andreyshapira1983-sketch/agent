from __future__ import annotations

from pathlib import Path

from core.architecture_audit import audit_architecture


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")


def test_architecture_audit_reports_multi_agent_blockers(tmp_path: Path):
    _touch(tmp_path / "core" / "team_plan.py")
    _touch(tmp_path / "tests" / "test_team_plan.py")
    _touch(tmp_path / "core" / "team_executor.py")
    _touch(tmp_path / "tests" / "test_team_executor.py")
    _touch(tmp_path / "core" / "budget_ledger.py")
    _touch(tmp_path / "tests" / "test_budget_ledger.py")

    audit = audit_architecture(tmp_path)
    payload = audit.to_dict()
    blockers = {
        item["id"]
        for item in payload["checks"]
        if item["blocks_multi_agent_execution"]
    }

    assert payload["multi_agent_state"] == "dry_run_executor_ready"
    assert payload["ready_for_multi_agent_execution"] is False
    assert "team_executor_dry_run" not in blockers
    assert "work_session_mode" in blockers
    assert "persistent_budget_windows" not in blockers
    assert "subagent_memory_scope" in blockers


def test_architecture_audit_summary_is_operator_readable(tmp_path: Path):
    audit = audit_architecture(tmp_path)
    summary = audit.user_summary(limit=3)

    assert "architecture audit" in summary
    assert "priority gaps" in summary
    assert "ready_for_multi_agent_execution" in summary


def test_architecture_audit_recognizes_doctrine_source_of_truth(tmp_path: Path):
    _touch(tmp_path / "AGENT_DOCTRINE.md")
    _touch(tmp_path / "архитектура автономного Агента.txt")
    _touch(tmp_path / "README.md")

    audit = audit_architecture(tmp_path)
    checks = {check.id: check for check in audit.checks}

    doctrine = checks["doctrine_and_architecture_docs"]
    assert doctrine.status == "present"
    assert "AGENT_DOCTRINE.md" in doctrine.evidence_files
