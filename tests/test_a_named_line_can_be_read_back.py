"""A line the agent can name is a line its tools must hand back.

Measured 2026-09-05 (exam session exam_i, turns 1-5, journal turns 28-32):
the agent found `core/loop_attempt.py:208` with findstr and then failed
three turns running to read that line — `file_read` had no window, and the
evidence budget kept a 12 000-char excerpt chosen by keyword. Asked about
its own previous turn, it read the newest OTHER trace (its subagent's) and
reported it as its session, because `read_logs` hides the live trace by
design and nothing ever told the agent which trace was its own.

Two doors, both opened here:
  * `file_read(path, start_line, end_line)` returns exactly that window;
  * `read_logs` names `live_trace_id` in every result and says so when it
    skipped the live log.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.planner import LLMPlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.read_logs import ReadLogsTool


@pytest.fixture
def numbered_file(workspace: Path) -> Path:
    target = workspace / "core" / "sample.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(f"line {n}" for n in range(1, 301)) + "\n", encoding="utf-8"
    )
    return target


class TestFileReadWindow:
    def test_without_a_window_the_whole_file_comes_back(self, workspace: Path, numbered_file: Path):
        out = FileReadTool(workspace).run("core/sample.py")
        assert out.startswith("line 1\n") and out.rstrip().endswith("line 300")

    def test_the_named_lines_come_back_numbered(self, workspace: Path, numbered_file: Path):
        out = FileReadTool(workspace).run("core/sample.py", start_line=207, end_line=209)
        assert out.splitlines() == [
            "[sample.py lines 207-209 of 300]",
            "207: line 207",
            "208: line 208",
            "209: line 209",
        ]

    def test_start_alone_opens_a_short_window(self, workspace: Path, numbered_file: Path):
        out = FileReadTool(workspace).run("core/sample.py", start_line=200)
        assert "[sample.py lines 200-259 of 300]" in out
        assert "259: line 259" in out and "260:" not in out

    def test_a_window_past_the_end_is_clipped_not_blank(self, workspace: Path, numbered_file: Path):
        out = FileReadTool(workspace).run("core/sample.py", start_line=298, end_line=999)
        assert "[sample.py lines 298-300 of 300]" in out

    def test_a_window_beyond_the_file_is_an_error(self, workspace: Path, numbered_file: Path):
        with pytest.raises(ValueError, match="has only 300 lines"):
            FileReadTool(workspace).run("core/sample.py", start_line=301)

    @pytest.mark.parametrize(
        ("start", "end", "message"),
        [
            (0, 5, "start_line must be >= 1"),
            (10, 5, "must not precede"),
            ("7", None, "must be an int"),
            (True, None, "must be an int"),
        ],
    )
    def test_a_malformed_window_is_refused(
        self, workspace: Path, numbered_file: Path, start, end, message
    ):
        with pytest.raises(ValueError, match=message):
            FileReadTool(workspace).run("core/sample.py", start_line=start, end_line=end)


def _sanitize(workspace: Path, steps):
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))

    class _StubLLM:
        def complete(self, **_kw):
            raise AssertionError("LLM must not be called in sanitiser tests")

    planner = LLMPlanner(llm=_StubLLM(), registry=reg)
    sources, warnings, _dropped = planner._validate_steps(steps, file_hint=None)
    return sources, warnings


class TestTheSanitizerLetsTheWindowThrough:
    def test_the_window_survives_admission(self, workspace: Path):
        sources, warnings = _sanitize(workspace, [{
            "tool": "file_read",
            "arguments": {"path": "core/loop_attempt.py", "start_line": 195, "end_line": 225},
        }])
        assert sources[0]["arguments"] == {
            "path": "core/loop_attempt.py", "start_line": 195, "end_line": 225,
        }
        assert sources[0]["label"] == "file:core/loop_attempt.py:195-225"
        assert warnings == []

    def test_a_string_number_is_read_as_a_number(self, workspace: Path):
        sources, _ = _sanitize(workspace, [{
            "tool": "file_read",
            "arguments": {"path": "core/x.py", "start_line": "208"},
        }])
        assert sources[0]["arguments"]["start_line"] == 208
        assert sources[0]["arguments"]["end_line"] == 208 + 59

    def test_a_reversed_window_is_a_planning_error(self, workspace: Path):
        sources, warnings = _sanitize(workspace, [{
            "tool": "file_read",
            "arguments": {"path": "core/x.py", "start_line": 50, "end_line": 10},
        }])
        assert sources == []
        assert any("precedes" in w for w in warnings)

    def test_an_oversized_window_is_clamped(self, workspace: Path):
        sources, warnings = _sanitize(workspace, [{
            "tool": "file_read",
            "arguments": {"path": "core/x.py", "start_line": 1, "end_line": 5000},
        }])
        assert sources[0]["arguments"]["end_line"] == 400
        assert any("clamped" in w for w in warnings)

    def test_no_window_means_no_window_keys(self, workspace: Path):
        sources, _ = _sanitize(workspace, [{
            "tool": "file_read", "arguments": {"path": "core/x.py"},
        }])
        assert sources[0]["arguments"] == {"path": "core/x.py"}
        assert sources[0]["label"] == "file:core/x.py"


def _write_trace(logs: Path, stem: str, events: list[str]) -> None:
    logs.mkdir(exist_ok=True)
    lines = [
        json.dumps({"ts": f"2026-09-05T12:00:{i:02d}+00:00", "trace_id": stem,
                    "event": ev, "payload": {}})
        for i, ev in enumerate(events)
    ]
    (logs / f"{stem}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestReadLogsNamesItsOwnTrace:
    def test_the_default_read_says_which_log_it_hid(self, tmp_path: Path):
        import os
        import time

        _write_trace(tmp_path / "logs", "trace_other", ["session_start", "respond"])
        time.sleep(0.02)
        _write_trace(tmp_path / "logs", "trace_live", ["session_start", "shell_exec", "respond"])
        now = time.time()
        os.utime(tmp_path / "logs" / "trace_live.jsonl", (now + 5, now + 5))

        out = ReadLogsTool(tmp_path, live_trace_id="trace_live").run()

        assert out["trace_id"] == "trace_other"
        assert out["skipped_live"] is True
        assert out["live_trace_id"] == "trace_live"
        assert "trace_id='trace_live'" in out["hint"]

    def test_the_live_trace_is_readable_by_name(self, tmp_path: Path):
        _write_trace(tmp_path / "logs", "trace_live", ["session_start", "shell_exec", "respond"])

        out = ReadLogsTool(tmp_path, live_trace_id="trace_live").run(
            trace_id="trace_live", event_filter=["shell_exec"],
        )

        assert out["is_live_session"] is True
        assert out["skipped_live"] is False
        assert [e["event"] for e in out["events"]] == ["shell_exec"]
        assert out["live_trace_id"] == "trace_live"
        assert "hint" not in out

    def test_without_a_live_id_the_field_is_empty_not_missing(self, tmp_path: Path):
        _write_trace(tmp_path / "logs", "trace_only", ["session_start"])
        out = ReadLogsTool(tmp_path).run()
        assert out["live_trace_id"] == ""
        assert "hint" not in out
