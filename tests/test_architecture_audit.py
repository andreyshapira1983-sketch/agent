from __future__ import annotations

from pathlib import Path

from core.architecture_audit import audit_architecture


def test_architecture_audit_summary_is_operator_readable(tmp_path: Path):
    audit = audit_architecture(tmp_path)
    summary = audit.user_summary(limit=3)

    assert "architecture audit" in summary
    assert "priority gaps" in summary
    assert "ready_for_multi_agent_execution" in summary
