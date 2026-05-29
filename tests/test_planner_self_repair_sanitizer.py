"""MVP-13.1 — planner sanitiser tests for self-repair primitives.

Mirror what we already do for `shell_exec` in `test_planner.py`:
exercise every drop path so a misbehaving LLM cannot smuggle a bad
argument shape past the planner gate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.planner import LLMPlanner
from tools.base import ToolRegistry
from tools.diff_file import DiffFileTool
from tools.file_read import FileReadTool
from tools.file_write import FileWriteTool
from tools.read_logs import ReadLogsTool
from tools.run_tests import RunTestsTool
from tools.shell_exec import ShellExecTool
from tools.web_search import WebSearchTool


def _planner(workspace: Path) -> LLMPlanner:
    """Build a planner whose registry knows every tool we need.

    `_validate_steps` is pure WRT the LLM client, but it does ask
    `self.registry.get(tool_name)` to reject unknown tools. So we
    construct a real registry with the self-repair primitives + the
    existing tools, plus a stubbed `llm` (the methods we exercise
    never touch it)."""
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    reg.register(WebSearchTool())
    reg.register(FileWriteTool(workspace_root=workspace))
    reg.register(ShellExecTool(workspace_root=workspace))
    reg.register(RunTestsTool(workspace_root=workspace))
    reg.register(ReadLogsTool(workspace_root=workspace))
    reg.register(DiffFileTool(workspace_root=workspace))

    class _StubLLM:
        def complete(self, **_kw):  # noqa: D401
            raise AssertionError("LLM must not be called in sanitiser tests")

    return LLMPlanner(llm=_StubLLM(), registry=reg)


def _run(
    workspace: Path, steps: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    return _planner(workspace)._validate_steps(steps, file_hint=None)


# ============================================================
# run_tests
# ============================================================

class TestRunTestsSanitizer:
    def test_defaults_pass(self, workspace: Path):
        sources, warnings = _run(workspace, [{"tool": "run_tests", "arguments": {}}])
        assert len(sources) == 1
        assert sources[0]["arguments"]["paths"] == ["tests"]
        assert "pattern" not in sources[0]["arguments"]
        assert warnings == []

    def test_explicit_paths_pass(self, workspace: Path):
        sources, _ = _run(workspace, [{
            "tool": "run_tests",
            "arguments": {"paths": ["tests/test_x.py", "tests/test_y.py"]},
        }])
        assert sources[0]["arguments"]["paths"] == [
            "tests/test_x.py", "tests/test_y.py"
        ]

    def test_pattern_pass_through(self, workspace: Path):
        sources, _ = _run(workspace, [{
            "tool": "run_tests",
            "arguments": {"pattern": "memory"},
        }])
        assert sources[0]["arguments"]["pattern"] == "memory"

    def test_non_list_paths_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "run_tests",
            "arguments": {"paths": "tests"},
        }])
        assert sources == []
        assert any("paths must be a list" in w for w in warnings)

    def test_too_many_paths_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "run_tests",
            "arguments": {"paths": [f"t{i}.py" for i in range(17)]},
        }])
        assert sources == []
        assert any("too long" in w for w in warnings)

    def test_non_string_path_element_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "run_tests",
            "arguments": {"paths": [123]},
        }])
        assert sources == []
        assert any("not a non-empty string" in w for w in warnings)

    def test_non_ascii_path_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "run_tests",
            "arguments": {"paths": ["тесты/"]},
        }])
        assert sources == []
        assert any("not ASCII" in w for w in warnings)

    def test_absolute_path_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "run_tests",
            "arguments": {"paths": ["/etc/passwd"]},
        }])
        assert sources == []
        assert any("looks absolute" in w for w in warnings)

    def test_drive_letter_path_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "run_tests",
            "arguments": {"paths": ["C:\\windows"]},
        }])
        assert sources == []
        assert any("looks absolute" in w for w in warnings)

    def test_parent_traversal_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "run_tests",
            "arguments": {"paths": ["../escape"]},
        }])
        assert sources == []
        assert any("'..'" in w for w in warnings)

    def test_non_string_pattern_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "run_tests",
            "arguments": {"pattern": 42},
        }])
        assert sources == []
        assert any("pattern must be a string" in w for w in warnings)

    def test_pattern_too_long_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "run_tests",
            "arguments": {"pattern": "x" * 250},
        }])
        assert sources == []
        assert any("pattern too long" in w for w in warnings)

    def test_non_ascii_pattern_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "run_tests",
            "arguments": {"pattern": "русский"},
        }])
        assert sources == []
        assert any("not ASCII" in w for w in warnings)

    def test_label_truncated(self, workspace: Path):
        long_paths = [f"tests/test_{i}.py" for i in range(10)]
        sources, _ = _run(workspace, [{
            "tool": "run_tests",
            "arguments": {"paths": long_paths},
        }])
        # Label cap kicks in at 60 chars.
        assert len(sources[0]["label"]) <= len("run_tests:") + 60


# ============================================================
# read_logs
# ============================================================

class TestReadLogsSanitizer:
    def test_defaults_pass(self, workspace: Path):
        sources, warnings = _run(workspace, [{"tool": "read_logs", "arguments": {}}])
        assert len(sources) == 1
        assert sources[0]["arguments"]["last_n"] == 50
        assert warnings == []

    def test_explicit_last_n_pass(self, workspace: Path):
        sources, _ = _run(workspace, [{"tool": "read_logs", "arguments": {"last_n": 10}}])
        assert sources[0]["arguments"]["last_n"] == 10

    def test_last_n_below_floor_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "read_logs",
            "arguments": {"last_n": 0},
        }])
        assert sources == []
        assert any("[1..500]" in w for w in warnings)

    def test_last_n_above_cap_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "read_logs",
            "arguments": {"last_n": 600},
        }])
        assert sources == []
        assert any("[1..500]" in w for w in warnings)

    def test_last_n_non_int_dropped(self, workspace: Path):
        sources, _ = _run(workspace, [{
            "tool": "read_logs",
            "arguments": {"last_n": "fifty"},
        }])
        assert sources == []

    def test_event_filter_pass(self, workspace: Path):
        sources, _ = _run(workspace, [{
            "tool": "read_logs",
            "arguments": {"event_filter": ["error", "replan"]},
        }])
        assert sources[0]["arguments"]["event_filter"] == ["error", "replan"]

    def test_event_filter_non_list_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "read_logs",
            "arguments": {"event_filter": "error"},
        }])
        assert sources == []
        assert any("must be a list" in w for w in warnings)

    def test_event_filter_too_long_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "read_logs",
            "arguments": {"event_filter": [f"e{i}" for i in range(25)]},
        }])
        assert sources == []
        assert any("too long" in w for w in warnings)

    def test_event_filter_non_ascii_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "read_logs",
            "arguments": {"event_filter": ["ошибка"]},
        }])
        assert sources == []
        assert any("not ASCII" in w for w in warnings)

    def test_event_filter_empty_string_dropped(self, workspace: Path):
        sources, _ = _run(workspace, [{
            "tool": "read_logs",
            "arguments": {"event_filter": [""]},
        }])
        assert sources == []

    def test_trace_id_pass(self, workspace: Path):
        sources, _ = _run(workspace, [{
            "tool": "read_logs",
            "arguments": {"trace_id": "run_abc123"},
        }])
        assert sources[0]["arguments"]["trace_id"] == "run_abc123"

    def test_trace_id_empty_dropped(self, workspace: Path):
        sources, _ = _run(workspace, [{
            "tool": "read_logs",
            "arguments": {"trace_id": ""},
        }])
        assert sources == []

    def test_trace_id_non_ascii_dropped(self, workspace: Path):
        sources, _ = _run(workspace, [{
            "tool": "read_logs",
            "arguments": {"trace_id": "русский"},
        }])
        assert sources == []


# ============================================================
# diff_file
# ============================================================

class TestDiffFileSanitizer:
    def test_well_formed_passes(self, workspace: Path):
        sources, _ = _run(workspace, [{
            "tool": "diff_file",
            "arguments": {
                "path": "core/loop.py",
                "proposed_content": "new content\n",
            },
        }])
        assert len(sources) == 1
        assert sources[0]["arguments"]["path"] == "core/loop.py"
        assert sources[0]["label"] == "diff_file:core/loop.py"

    def test_label_does_not_echo_proposed_content(self, workspace: Path):
        secret_marker = "PROPOSED_PAYLOAD_MARKER"
        sources, _ = _run(workspace, [{
            "tool": "diff_file",
            "arguments": {
                "path": "a.txt",
                "proposed_content": secret_marker,
            },
        }])
        assert secret_marker not in sources[0]["label"]

    def test_missing_path_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "diff_file",
            "arguments": {"proposed_content": "x"},
        }])
        assert sources == []
        assert any("without path" in w for w in warnings)

    def test_non_ascii_path_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "diff_file",
            "arguments": {"path": "привет.txt", "proposed_content": "x"},
        }])
        assert sources == []
        assert any("not ASCII" in w for w in warnings)

    def test_absolute_path_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "diff_file",
            "arguments": {"path": "/etc/passwd", "proposed_content": "x"},
        }])
        assert sources == []
        assert any("looks absolute" in w for w in warnings)

    def test_drive_letter_path_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "diff_file",
            "arguments": {"path": "C:\\foo.txt", "proposed_content": "x"},
        }])
        assert sources == []
        assert any("looks absolute" in w for w in warnings)

    def test_parent_traversal_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "diff_file",
            "arguments": {"path": "../escape.txt", "proposed_content": "x"},
        }])
        assert sources == []
        assert any("'..'" in w for w in warnings)

    def test_non_string_proposed_dropped(self, workspace: Path):
        sources, warnings = _run(workspace, [{
            "tool": "diff_file",
            "arguments": {"path": "a.txt", "proposed_content": 42},
        }])
        assert sources == []
        assert any("must be a string" in w for w in warnings)

    def test_context_lines_out_of_range_dropped(self, workspace: Path):
        sources, _ = _run(workspace, [{
            "tool": "diff_file",
            "arguments": {
                "path": "a.txt",
                "proposed_content": "x",
                "context_lines": 25,
            },
        }])
        assert sources == []

    def test_context_lines_non_int_dropped(self, workspace: Path):
        sources, _ = _run(workspace, [{
            "tool": "diff_file",
            "arguments": {
                "path": "a.txt",
                "proposed_content": "x",
                "context_lines": 2.5,
            },
        }])
        assert sources == []
