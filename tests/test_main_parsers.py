"""Unit tests for the pure parsing/validation helpers in main.py.

These functions decode operator CLI input and enforce the workspace-jail and
ASCII-identifier policies. They are pure (no agent, no I/O beyond a filesystem
check) but were largely unexercised — yet they are load-bearing: a wrong parse
silently mis-routes an operator command, and a hole in
`_resolve_workspace_text_file` is a path-traversal escape.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cli.parsers import (
    _env_bool,
    _parse_ingest_options,
    _parse_remember,
    _parse_repair_generation_args,
    _parse_source_planning_args,
    _resolve_workspace_text_file,
    _split_meta_args,
)

# ============================================================
# _env_bool
# ============================================================

class TestEnvBool:
    @pytest.mark.parametrize("raw", ["1", "true", "YES", " On ", "approve", "TRUE"])
    def test_truthy_values(self, monkeypatch, raw):
        monkeypatch.setenv("AGENT_TEST_FLAG", raw)
        assert _env_bool("AGENT_TEST_FLAG") is True


# ============================================================
# _parse_remember
# ============================================================

class TestParseRemember:
    def test_empty_input(self):
        assert _parse_remember("   ") == ([], "")


# ============================================================
# _resolve_workspace_text_file  (path-traversal jail)
# ============================================================

class TestResolveWorkspaceTextFile:
    def test_empty_path_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError):
            _resolve_workspace_text_file(tmp_path, "", role="note")

    def test_non_ascii_path_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError):
            _resolve_workspace_text_file(tmp_path, "заметка.txt", role="note")

    def test_absolute_path_rejected(self, tmp_path: Path):
        with pytest.raises(PermissionError):
            _resolve_workspace_text_file(tmp_path, "/etc/passwd", role="note")

    def test_dotdot_escape_rejected(self, tmp_path: Path):
        with pytest.raises(PermissionError):
            _resolve_workspace_text_file(tmp_path, "../secret.txt", role="note")

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            _resolve_workspace_text_file(tmp_path, "gone.txt", role="note")


# ============================================================
# _parse_repair_generation_args
# ============================================================

class TestParseRepairGenerationArgs:
    def test_empty_returns_usage_error(self):
        target, _tests, _pattern, _trace, error = _parse_repair_generation_args("")
        assert target is None
        assert error and error.startswith("Usage:")


    def test_pattern_and_trace_flags(self):
        target, _tests, pattern, trace, error = _parse_repair_generation_args(
            "core/foo.py --pattern test_x --trace t-42"
        )
        assert target == "core/foo.py"
        assert pattern == "test_x"
        assert trace == "t-42"
        assert error is None

    def test_pattern_missing_value(self):
        _, _, _, _, error = _parse_repair_generation_args("core/foo.py --pattern")
        assert error and "--pattern requires a value" in error

    def test_trace_missing_value(self):
        _, _, _, _, error = _parse_repair_generation_args("core/foo.py --trace")
        assert error and "--trace requires a value" in error


# ============================================================
# _split_meta_args
# ============================================================

class TestSplitMetaArgs:
    def test_unbalanced_quotes_fall_back_to_plain_split(self):
        # shlex would raise on an unterminated quote; fallback uses str.split.
        assert _split_meta_args('a "b') == ["a", "b"]


# ============================================================
# _parse_ingest_options
# ============================================================

class TestParseIngestOptions:
    def test_no_memory_flag(self):
        _, _, auto, _, _ = _parse_ingest_options("docs --no-memory", default_path=None)
        assert auto is False


    def test_limit_missing_value(self):
        _, _, _, _, error = _parse_ingest_options("docs --limit", default_path=None)
        assert error and "--limit requires a number" in error

    def test_limit_non_numeric(self):
        _, _, _, _, error = _parse_ingest_options("docs --limit abc", default_path=None)
        assert error and "--limit requires a number" in error

    def test_limit_below_one(self):
        _, _, _, _, error = _parse_ingest_options("docs --limit 0", default_path=None)
        assert error and "--limit must be >= 1" in error


    def test_no_path_and_no_default_is_error(self):
        path, _, _, _, error = _parse_ingest_options("--dry-run", default_path=None)
        assert path is None
        assert error and error.startswith("Usage:")


# ============================================================
# _parse_source_planning_args
# ============================================================

class TestParseSourcePlanningArgs:
    USAGE = ":source-review-plan <goal> [--limit N] [--json]"


    def test_limit_flag(self):
        _as_json, limit, goal = _parse_source_planning_args(
            "topic --limit 3", usage=self.USAGE
        )
        assert limit == 3
        assert goal == "topic"


    def test_limit_below_one_returns_none(self, capsys):
        assert _parse_source_planning_args("topic --limit 0", usage=self.USAGE) is None
        assert "--limit must be >= 1" in capsys.readouterr().err
