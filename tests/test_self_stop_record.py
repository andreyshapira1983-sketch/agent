import pytest

from core.self_stop_record import record_self_stop, ALLOWED_PATH, ALLOWED_KINDS
from core.state_integrity import read_state_jsonl_unlocked


@pytest.fixture
def chdir_to_tmp(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_record_is_written(chdir_to_tmp):
    result = record_self_stop(
        episode_id="ep_1",
        run_id="run_1",
        kind="budget_stop",
        ts="2026-09-01T00:00:00Z",
        source="test",
        verdict_ref="v1",
        reason="budget exhausted",
        outcome="stopped",
    )
    assert result["recorded"] is True
    assert result["signature"]
    records = read_state_jsonl_unlocked(ALLOWED_PATH)
    assert len(records) == 1
    assert records[0]["kind"] == "budget_stop"
    assert records[0]["source"] == "test"
    assert records[0]["signature"] == result["signature"]


def test_rejects_missing_source(chdir_to_tmp):
    result = record_self_stop(
        episode_id="ep_1",
        run_id="run_1",
        kind="budget_stop",
        ts="2026-09-01T00:00:00Z",
        source="",
        verdict_ref="v1",
        reason="budget exhausted",
        outcome="stopped",
    )
    assert result["recorded"] is False
    assert "error" in result
    assert not (chdir_to_tmp / "data" / "self_stops.jsonl").exists()


def test_rejects_foreign_kind(chdir_to_tmp):
    result = record_self_stop(
        episode_id="ep_1",
        run_id="run_1",
        kind="not_a_real_kind",
        ts="2026-09-01T00:00:00Z",
        source="test",
        verdict_ref="v1",
        reason="budget exhausted",
        outcome="stopped",
    )
    assert result["recorded"] is False
    assert "error" in result
    assert not (chdir_to_tmp / "data" / "self_stops.jsonl").exists()


def test_signature_is_deterministic(chdir_to_tmp):
    r1 = record_self_stop(
        episode_id="ep_1",
        run_id="run_1",
        kind="budget_stop",
        ts="2026-09-01T00:00:00Z",
        source="test",
        verdict_ref="v1",
        reason="budget   exhausted",
        outcome="stopped",
    )
    r2 = record_self_stop(
        episode_id="ep_2",
        run_id="run_2",
        kind="budget_stop",
        ts="2026-09-01T00:00:00Z",
        source="test",
        verdict_ref="v1",
        reason="budget exhausted",
        outcome="stopped",
    )
    r3 = record_self_stop(
        episode_id="ep_3",
        run_id="run_3",
        kind="budget_stop",
        ts="2026-09-01T00:00:00Z",
        source="test",
        verdict_ref="v1",
        reason="different reason",
        outcome="stopped",
    )
    assert r1["signature"] == r2["signature"]
    assert r1["signature"] != r3["signature"]


def test_record_self_stop_is_not_a_tool_name():
    import pathlib
    import re

    names = set()
    for path in pathlib.Path("tools").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"^class\s+(\w+)", text, re.MULTILINE):
            names.add(m.group(1))
    assert "record_self_stop" not in names
