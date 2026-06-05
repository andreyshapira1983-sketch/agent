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

from main import (
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
    def test_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv("AGENT_TEST_FLAG", raising=False)
        assert _env_bool("AGENT_TEST_FLAG") is False
        assert _env_bool("AGENT_TEST_FLAG", default=True) is True

    @pytest.mark.parametrize("raw", ["1", "true", "YES", " On ", "approve", "TRUE"])
    def test_truthy_values(self, monkeypatch, raw):
        monkeypatch.setenv("AGENT_TEST_FLAG", raw)
        assert _env_bool("AGENT_TEST_FLAG") is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "", "maybe"])
    def test_falsey_values(self, monkeypatch, raw):
        monkeypatch.setenv("AGENT_TEST_FLAG", raw)
        assert _env_bool("AGENT_TEST_FLAG") is False


# ============================================================
# _parse_remember
# ============================================================

class TestParseRemember:
    def test_empty_input(self):
        assert _parse_remember("   ") == ([], "")

    def test_no_tags_uses_default(self):
        tags, content = _parse_remember("the sky is blue")
        assert tags == ["user-approved"]
        assert content == "the sky is blue"

    def test_known_single_tag(self):
        tags, content = _parse_remember("preference dark mode please")
        assert tags == ["preference"]
        assert content == "dark mode please"

    def test_comma_separated_tags(self):
        tags, content = _parse_remember("fact,decision shipped v2")
        assert tags == ["fact", "decision"]
        assert content == "shipped v2"

    def test_non_ascii_tags_fall_back_to_user_approved(self):
        # A comma triggers tag-parsing, but the cyrillic tag is dropped.
        tags, content = _parse_remember("факт, мнение тело заметки")
        assert tags == ["user-approved"]
        assert content == "мнение тело заметки"

    def test_unicode_content_is_preserved(self):
        tags, content = _parse_remember("insight пользователь любит краткость")
        assert tags == ["insight"]
        assert content == "пользователь любит краткость"


# ============================================================
# _resolve_workspace_text_file  (path-traversal jail)
# ============================================================

class TestResolveWorkspaceTextFile:
    def test_valid_file_inside_workspace(self, tmp_path: Path):
        target = tmp_path / "notes.txt"
        target.write_text("hi", encoding="utf-8")
        resolved = _resolve_workspace_text_file(tmp_path, "notes.txt", role="note")
        assert resolved == target.resolve()

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
        target, tests, pattern, trace, error = _parse_repair_generation_args("")
        assert target is None
        assert error and error.startswith("Usage:")

    def test_default_test_path(self):
        target, tests, pattern, trace, error = _parse_repair_generation_args("core/foo.py")
        assert target == "core/foo.py"
        assert tests == ("tests",)
        assert pattern is None and trace is None and error is None

    def test_explicit_test_paths(self):
        target, tests, *_ = _parse_repair_generation_args("core/foo.py tests/a.py tests/b.py")
        assert tests == ("tests/a.py", "tests/b.py")

    def test_pattern_and_trace_flags(self):
        target, tests, pattern, trace, error = _parse_repair_generation_args(
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
    def test_empty(self):
        assert _split_meta_args("   ") == []

    def test_quoted_tokens_are_unwrapped(self):
        assert _split_meta_args('"hello world" foo') == ["hello world", "foo"]

    def test_unbalanced_quotes_fall_back_to_plain_split(self):
        # shlex would raise on an unterminated quote; fallback uses str.split.
        assert _split_meta_args('a "b') == ["a", "b"]


# ============================================================
# _parse_ingest_options
# ============================================================

class TestParseIngestOptions:
    def test_path_only(self):
        path, dry, auto, limit, error = _parse_ingest_options("docs/readme.md", default_path=None)
        assert path == "docs/readme.md"
        assert dry is False and auto is None and error is None

    def test_flags(self):
        path, dry, auto, limit, error = _parse_ingest_options(
            "docs --dry-run --write-memory", default_path=None
        )
        assert path == "docs"
        assert dry is True
        assert auto is True

    def test_no_memory_flag(self):
        _, _, auto, _, _ = _parse_ingest_options("docs --no-memory", default_path=None)
        assert auto is False

    def test_limit_value(self):
        _, _, _, limit, error = _parse_ingest_options("docs --limit 12", default_path=None)
        assert limit == 12
        assert error is None

    def test_limit_missing_value(self):
        _, _, _, _, error = _parse_ingest_options("docs --limit", default_path=None)
        assert error and "--limit requires a number" in error

    def test_limit_non_numeric(self):
        _, _, _, _, error = _parse_ingest_options("docs --limit abc", default_path=None)
        assert error and "--limit requires a number" in error

    def test_limit_below_one(self):
        _, _, _, _, error = _parse_ingest_options("docs --limit 0", default_path=None)
        assert error and "--limit must be >= 1" in error

    def test_default_path_used_when_no_paths(self):
        path, _, _, _, error = _parse_ingest_options("--dry-run", default_path="fallback")
        assert path == "fallback"
        assert error is None

    def test_no_path_and_no_default_is_error(self):
        path, _, _, _, error = _parse_ingest_options("--dry-run", default_path=None)
        assert path is None
        assert error and error.startswith("Usage:")

    def test_multi_word_path_is_joined(self):
        path, _, _, _, _ = _parse_ingest_options("my folder name", default_path=None)
        assert path == "my folder name"


# ============================================================
# _parse_source_planning_args
# ============================================================

class TestParseSourcePlanningArgs:
    USAGE = ":source-review-plan <goal> [--limit N] [--json]"

    def test_goal_only(self):
        result = _parse_source_planning_args("learn rust", usage=self.USAGE)
        assert result == (False, 8, "learn rust")

    def test_json_flag(self):
        as_json, limit, goal = _parse_source_planning_args("learn rust --json", usage=self.USAGE)
        assert as_json is True
        assert limit == 8
        assert goal == "learn rust"

    def test_limit_flag(self):
        as_json, limit, goal = _parse_source_planning_args(
            "topic --limit 3", usage=self.USAGE
        )
        assert limit == 3
        assert goal == "topic"

    def test_limit_missing_value_returns_none(self, capsys):
        assert _parse_source_planning_args("topic --limit", usage=self.USAGE) is None
        assert "--limit requires a number" in capsys.readouterr().err

    def test_limit_non_numeric_returns_none(self, capsys):
        assert _parse_source_planning_args("topic --limit x", usage=self.USAGE) is None
        assert "--limit requires a number" in capsys.readouterr().err

    def test_limit_below_one_returns_none(self, capsys):
        assert _parse_source_planning_args("topic --limit 0", usage=self.USAGE) is None
        assert "--limit must be >= 1" in capsys.readouterr().err
