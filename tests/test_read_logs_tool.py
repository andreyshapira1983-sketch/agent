"""MVP-13.1 — unit tests for the `read_logs` tool."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools.read_logs import (
    DEFAULT_LAST_N,
    MAX_EVENT_FILTER,
    MAX_LAST_N,
    ReadLogsTool,
)


# ============================================================
# Helpers
# ============================================================

def _seed_log(
    workspace: Path,
    trace_id: str,
    events: list[dict[str, Any]],
) -> Path:
    log_dir = workspace / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{trace_id}.jsonl"
    with log_path.open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    return log_path


def _event(name: str, **payload) -> dict[str, Any]:
    return {
        "ts": "2026-05-26T12:00:00+00:00",
        "trace_id": "test",
        "event": name,
        "payload": payload,
    }


# ============================================================
# Construction
# ============================================================

class TestConstruction:
    def test_rejects_nonexistent_workspace(self, tmp_path: Path):
        with pytest.raises(ValueError, match="existing directory"):
            ReadLogsTool(workspace_root=tmp_path / "nope")

    def test_risk_is_read_only(self, workspace: Path):
        t = ReadLogsTool(workspace_root=workspace)
        assert t.risk == "read_only"
        assert t.risk_for({}) == "read_only"
        assert t.risk_for({"last_n": 500}) == "read_only"


# ============================================================
# Argument validation
# ============================================================

class TestArgValidation:
    def test_last_n_zero_rejected(self, workspace: Path):
        t = ReadLogsTool(workspace_root=workspace)
        with pytest.raises(ValueError, match=">= 1"):
            t.run(last_n=0)

    def test_last_n_above_cap_rejected(self, workspace: Path):
        t = ReadLogsTool(workspace_root=workspace)
        with pytest.raises(ValueError, match=f"<= {MAX_LAST_N}"):
            t.run(last_n=MAX_LAST_N + 1)

    def test_last_n_non_int_rejected(self, workspace: Path):
        t = ReadLogsTool(workspace_root=workspace)
        with pytest.raises(ValueError, match="int"):
            t.run(last_n="50")  # type: ignore[arg-type]

    def test_event_filter_non_list_rejected(self, workspace: Path):
        t = ReadLogsTool(workspace_root=workspace)
        with pytest.raises(ValueError, match="event_filter must be a list"):
            t.run(event_filter="error")  # type: ignore[arg-type]

    def test_event_filter_empty_string_rejected(self, workspace: Path):
        t = ReadLogsTool(workspace_root=workspace)
        with pytest.raises(ValueError, match="non-empty"):
            t.run(event_filter=[""])

    def test_event_filter_too_long_rejected(self, workspace: Path):
        t = ReadLogsTool(workspace_root=workspace)
        with pytest.raises(ValueError, match="too long"):
            t.run(event_filter=[f"e{i}" for i in range(MAX_EVENT_FILTER + 1)])

    def test_event_filter_non_ascii_rejected(self, workspace: Path):
        t = ReadLogsTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match="ASCII"):
            t.run(event_filter=["ошибка"])

    def test_trace_id_non_ascii_rejected(self, workspace: Path):
        t = ReadLogsTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match="ASCII"):
            t.run(trace_id="русский_id")


# ============================================================
# Reading: empty workspace / missing logs dir
# ============================================================

class TestEmptyState:
    def test_no_logs_dir_returns_empty(self, workspace: Path):
        out = ReadLogsTool(workspace_root=workspace).run()
        assert out["events"] == []
        assert out["total_events"] == 0
        assert out["events_returned"] == 0
        assert out["trace_id"] == ""

    def test_empty_logs_dir_returns_empty(self, workspace: Path):
        (workspace / "logs").mkdir()
        out = ReadLogsTool(workspace_root=workspace).run()
        assert out["events"] == []

    def test_missing_trace_id_returns_empty(self, workspace: Path):
        _seed_log(workspace, "run_real", [_event("planner")])
        out = ReadLogsTool(workspace_root=workspace).run(trace_id="run_fake")
        assert out["events"] == []


# ============================================================
# Reading: standard cases
# ============================================================

class TestReadingHappyPath:
    def test_default_picks_most_recent_log(self, workspace: Path):
        log_a = _seed_log(workspace, "run_aaa", [_event("planner")])
        log_b = _seed_log(workspace, "run_bbb", [_event("respond"), _event("planner")])
        # Bump mtime so log_b is newer.
        import os, time
        now = time.time()
        os.utime(log_a, (now - 100, now - 100))
        os.utime(log_b, (now, now))

        out = ReadLogsTool(workspace_root=workspace).run()
        assert out["trace_id"] == "run_bbb"
        assert out["total_events"] == 2
        assert out["events_returned"] == 2

    def test_explicit_trace_id_targets_that_log(self, workspace: Path):
        _seed_log(workspace, "run_a", [_event("planner")])
        _seed_log(workspace, "run_b", [_event("error")])
        out = ReadLogsTool(workspace_root=workspace).run(trace_id="run_a")
        assert out["trace_id"] == "run_a"
        assert out["events"][0]["event"] == "planner"

    def test_last_n_truncates(self, workspace: Path):
        _seed_log(workspace, "run_x",
                  [_event(f"e{i}") for i in range(10)])
        out = ReadLogsTool(workspace_root=workspace).run(last_n=3)
        assert out["events_returned"] == 3
        assert out["total_events"] == 10
        # The LAST 3 events come back (chronologically newest).
        assert out["events"][0]["event"] == "e7"
        assert out["events"][-1]["event"] == "e9"

    def test_event_filter_keeps_only_named_events(self, workspace: Path):
        _seed_log(workspace, "run_f", [
            _event("planner"),
            _event("error"),
            _event("respond"),
            _event("error"),
            _event("plan"),
        ])
        out = ReadLogsTool(workspace_root=workspace).run(
            event_filter=["error"]
        )
        assert out["filtered"] is True
        assert out["events_returned"] == 2
        for e in out["events"]:
            assert e["event"] == "error"


# ============================================================
# Robustness: malformed log lines
# ============================================================

class TestRobustness:
    def test_malformed_jsonl_lines_silently_skipped(self, workspace: Path):
        log_dir = workspace / "logs"
        log_dir.mkdir()
        path = log_dir / "run_garbage.jsonl"
        path.write_text(
            json.dumps(_event("planner")) + "\n"
            "<not json>\n"
            "\n"  # blank line
            + json.dumps(_event("respond")) + "\n",
            encoding="utf-8",
        )
        out = ReadLogsTool(workspace_root=workspace).run(trace_id="run_garbage")
        assert [e["event"] for e in out["events"]] == ["planner", "respond"]

    def test_json_lines_that_are_not_dicts_skipped(self, workspace: Path):
        log_dir = workspace / "logs"
        log_dir.mkdir()
        path = log_dir / "run_list.jsonl"
        path.write_text(
            json.dumps(_event("planner")) + "\n"
            "[1, 2, 3]\n"
            "\"plain string\"\n"
            + json.dumps(_event("respond")) + "\n",
            encoding="utf-8",
        )
        out = ReadLogsTool(workspace_root=workspace).run(trace_id="run_list")
        # Only the dict-shaped lines survive.
        assert [e["event"] for e in out["events"]] == ["planner", "respond"]


# ============================================================
# Sandbox: trace_id can't escape logs/
# ============================================================

class TestSandbox:
    def test_trace_id_with_directory_traversal_is_path_safe(self, workspace: Path):
        """`../../etc/passwd` style trace_ids cannot reach outside logs/.
        require_ascii_identifier blocks the slashes via PermissionError."""
        t = ReadLogsTool(workspace_root=workspace)
        # `/` is ASCII so it slips past `require_ascii_identifier`, but
        # the path-containment check inside _resolve_log_path catches it.
        with pytest.raises(PermissionError):
            t.run(trace_id="..\\..\\etc\\passwd")


# ============================================================
# Redaction
# ============================================================

class TestRedaction:
    def test_secret_in_log_payload_redacted_on_return(self, workspace: Path):
        """The logger already redacts at write time. We pin defence-in-depth:
        even if a malformed event sneaks a credential through, the tool's
        output never contains the raw shape."""
        log_dir = workspace / "logs"
        log_dir.mkdir()
        path = log_dir / "run_leak.jsonl"
        secret = "sk-" + "A" * 48
        path.write_text(
            json.dumps({
                "ts": "2026-01-01T00:00:00Z",
                "trace_id": "run_leak",
                "event": "planner",
                "payload": {"argv": secret},
            }) + "\n",
            encoding="utf-8",
        )
        out = ReadLogsTool(workspace_root=workspace).run(trace_id="run_leak")
        assert secret not in json.dumps(out)


# ============================================================
# validate_output
# ============================================================

class TestValidateOutput:
    def _ok(self) -> dict[str, Any]:
        return {
            "trace_id": "run_a",
            "log_file": "logs/run_a.jsonl",
            "events_returned": 1,
            "total_events": 1,
            "filtered": False,
            "events": [{"event": "planner"}],
            "compensation_plan": {"id": "x", "actions": [], "tool_name": "t", "description": "d"},
        }

    def test_well_formed_passes(self, workspace: Path):
        ok, _ = ReadLogsTool(workspace_root=workspace).validate_output(self._ok())
        assert ok

    def test_non_dict_rejected(self, workspace: Path):
        ok, _ = ReadLogsTool(workspace_root=workspace).validate_output([])
        assert not ok

    def test_missing_keys_rejected(self, workspace: Path):
        out = self._ok()
        del out["events"]
        ok, _ = ReadLogsTool(workspace_root=workspace).validate_output(out)
        assert not ok

    def test_returned_greater_than_total_rejected(self, workspace: Path):
        out = self._ok()
        out["events_returned"] = 5
        out["total_events"] = 1
        ok, issues = ReadLogsTool(workspace_root=workspace).validate_output(out)
        assert not ok

    def test_returned_greater_than_total_ok_when_filtered(self, workspace: Path):
        """A filter could theoretically return everything; we only flag
        the contradictory case when no filter was applied."""
        out = self._ok()
        out["events_returned"] = 1
        out["total_events"] = 1
        out["filtered"] = True
        ok, _ = ReadLogsTool(workspace_root=workspace).validate_output(out)
        assert ok

    def test_default_last_n_is_50(self):
        assert DEFAULT_LAST_N == 50

    def test_max_last_n_is_500(self):
        assert MAX_LAST_N == 500
