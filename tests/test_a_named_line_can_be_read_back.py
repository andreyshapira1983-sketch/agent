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

from core.evidence import ProvenanceChain, evidence_from_tool_result
from core.planner import LLMPlanner
from core.verifier_utils import match_citation, parse_citations
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.read_logs import ReadLogsTool


@pytest.fixture(name="numbered_file")
def _numbered_file(workspace: Path) -> Path:
    target = workspace / "core" / "sample.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(f"line {n}" for n in range(1, 301)) + "\n", encoding="utf-8"
    )
    return target


class TestFileReadWindow:
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


class TestAWindowedReadIsCitable:
    """Turn 33 (2026-09-05, session exam_j): line 208 quoted correctly, and the
    verifier judged all six citations fabricated — the plan step was labelled
    `file:core/loop_attempt.py:200-215`, the evidence `file:core/loop_attempt.py`.
    The window is part of the source's name on both sides now, and a citation
    without a window still lands on the file."""

    @staticmethod
    def _chain(*reads: tuple[dict, str]) -> ProvenanceChain:
        chain = ProvenanceChain()
        for arguments, output in reads:
            chain.add(evidence_from_tool_result(
                tool_name="file_read", arguments=arguments,
                output=output, status="success",
            ))
        return chain


    def test_the_citation_lands_on_its_own_window_first(self):
        chain = self._chain(
            ({"path": "core/loop_attempt.py", "start_line": 1, "end_line": 120}, "1: head"),
            ({"path": "core/loop_attempt.py", "start_line": 200, "end_line": 215}, "208: tail"),
        )
        cit = parse_citations("факт [file:core/loop_attempt.py:200-215].")[0]
        hit = match_citation(cit, chain)
        assert hit is not None and hit.source_id == "file:core/loop_attempt.py:200-215"

    def test_a_citation_without_a_window_still_finds_the_file(self):
        chain = self._chain(
            ({"path": "core/loop_attempt.py", "start_line": 200, "end_line": 215}, "208: tail"),
        )
        cit = parse_citations("факт [file:core/loop_attempt.py].")[0]
        assert match_citation(cit, chain) is not None
