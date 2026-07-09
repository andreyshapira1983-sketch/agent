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


def test_active_file_integrity_ok_returns_false_for_missing_file(tmp_path: Path):
    from core.state_store_drill import _active_file_integrity_ok
    assert _active_file_integrity_ok(tmp_path / "nope.jsonl") is False


def test_active_file_integrity_ok_returns_false_for_empty_file(tmp_path: Path):
    from core.state_store_drill import _active_file_integrity_ok
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    assert _active_file_integrity_ok(p) is False


def test_active_file_integrity_ok_returns_false_for_invalid_json(tmp_path: Path):
    from core.state_store_drill import _active_file_integrity_ok
    p = tmp_path / "bad.jsonl"
    p.write_text("{not-json\n", encoding="utf-8")
    assert _active_file_integrity_ok(p) is False


def test_active_file_integrity_ok_returns_false_when_integrity_field_missing(tmp_path: Path):
    from core.state_store_drill import _active_file_integrity_ok
    import json
    p = tmp_path / "no_envelope.jsonl"
    p.write_text(json.dumps({"foo": "bar"}) + "\n", encoding="utf-8")
    assert _active_file_integrity_ok(p) is False


def test_active_file_integrity_ok_returns_false_when_envelope_hash_corrupt(tmp_path: Path):
    from core.state_store_drill import _active_file_integrity_ok
    import json
    p = tmp_path / "bad_hash.jsonl"
    row = {
        "_integrity": {"format": "agent-state-v1", "alg": "sha256", "hash": "deadbeef"},
        "payload": {"goal": "x"},
    }
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert _active_file_integrity_ok(p) is False


def test_active_file_integrity_ok_returns_true_for_valid_envelope(tmp_path: Path):
    from core.state_integrity import encode_state_row
    from core.state_store_drill import _active_file_integrity_ok
    p = tmp_path / "ok.jsonl"
    p.write_text(encode_state_row({"goal": "x"}) + "\n", encoding="utf-8")
    assert _active_file_integrity_ok(p) is True
