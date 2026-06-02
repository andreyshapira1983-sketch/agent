from __future__ import annotations

from pathlib import Path

from core.state_store_drill import run_state_store_drill


def test_state_store_drill_quarantines_and_recovers_isolated_file(tmp_path: Path):
    report = run_state_store_drill(tmp_path)
    payload = report.to_dict()

    assert payload["status"] == "passed"
    assert payload["recovered_rows"] == 1
    assert payload["quarantined_files"] == 1
    assert payload["active_file_integrity_ok"] is True
    assert payload["secret_redacted"] is True
    assert all(payload["checks"].values())
    assert Path(payload["path"]).is_relative_to(tmp_path)
    assert "state_store_drills" in payload["path"]
    assert "status=passed" in report.user_summary()
