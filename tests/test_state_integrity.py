from __future__ import annotations

import json
from pathlib import Path

from core.state_integrity import (
    decode_state_row,
    encode_state_row,
    quarantine_dir_for,
    read_state_jsonl,
    rewrite_state_jsonl,
)


def test_state_row_round_trip_with_checksum():
    line = encode_state_row({"kind": "x", "value": 1})

    row = json.loads(line)
    assert row["_integrity"]["alg"] == "sha256"
    assert decode_state_row(line) == {"kind": "x", "value": 1}


def test_read_accepts_legacy_rows_and_upgrades_to_envelopes(tmp_path: Path):
    path = tmp_path / "state.jsonl"
    path.write_text('{"kind":"legacy","value":2}\n', encoding="utf-8")

    rows = read_state_jsonl(path)

    assert rows == [{"kind": "legacy", "value": 2}]
    upgraded = path.read_text(encoding="utf-8")
    assert "_integrity" in upgraded
    assert "payload" in upgraded


def test_corrupt_rows_are_quarantined_and_removed(tmp_path: Path):
    path = tmp_path / "state.jsonl"
    good = encode_state_row({"ok": True})
    path.write_text(good + "\nnot-json\n", encoding="utf-8")

    rows = read_state_jsonl(path)

    assert rows == [{"ok": True}]
    assert "not-json" not in path.read_text(encoding="utf-8")
    quarantines = list(quarantine_dir_for(path).glob("state.jsonl.*.bad.jsonl"))
    assert len(quarantines) == 1
    assert "not-json" in quarantines[0].read_text(encoding="utf-8")


def test_quarantine_redacts_sensitive_raw_rows(tmp_path: Path):
    path = tmp_path / "state.jsonl"
    path.write_text("not-json API_KEY=supersecret123 andre@example.com\n", encoding="utf-8")

    rows = read_state_jsonl(path)

    assert rows == []
    quarantine_text = next(quarantine_dir_for(path).glob("state.jsonl.*.bad.jsonl")).read_text(
        encoding="utf-8"
    )
    assert "supersecret123" not in quarantine_text
    assert "andre@example.com" not in quarantine_text
    assert "[REDACTED:" in quarantine_text


def test_checksum_mismatch_is_quarantined(tmp_path: Path):
    path = tmp_path / "state.jsonl"
    line = encode_state_row({"value": "original"}).replace("original", "tampered")
    path.write_text(line + "\n", encoding="utf-8")

    rows = read_state_jsonl(path)

    assert rows == []
    assert path.read_text(encoding="utf-8") == ""
    quarantines = list(quarantine_dir_for(path).glob("state.jsonl.*.bad.jsonl"))
    assert len(quarantines) == 1
    assert "checksum mismatch" in quarantines[0].read_text(encoding="utf-8")


def test_rewrite_state_jsonl_writes_only_checksummed_rows(tmp_path: Path):
    path = tmp_path / "state.jsonl"

    rewrite_state_jsonl(path, [{"a": 1}, {"b": 2}])

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [decode_state_row(line) for line in lines] == [{"a": 1}, {"b": 2}]
