"""Tests for tools/builtins/file_write.py — FileWriteTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.builtins.file_write import FileWriteTool


# ════════════════════════════════════════════════════════════════════
# Happy path
# ════════════════════════════════════════════════════════════════════

class TestCreate:

    def test_writes_relative_to_workspace(self, tmp_path):
        tool = FileWriteTool(workspace_root=tmp_path)
        result = tool.execute(path="notes/hello.md", content="# Hello")
        assert result.success
        assert (tmp_path / "notes" / "hello.md").read_text(encoding="utf-8") == "# Hello"
        assert result.output["existed"] is False

    def test_refuses_overwrite_by_default(self, tmp_path):
        tool = FileWriteTool(workspace_root=tmp_path)
        (tmp_path / "f.txt").write_text("old", encoding="utf-8")
        result = tool.execute(path="f.txt", content="new")
        assert not result.success
        assert "exists" in result.error

    def test_overwrite_replaces(self, tmp_path):
        tool = FileWriteTool(workspace_root=tmp_path)
        (tmp_path / "f.txt").write_text("old", encoding="utf-8")
        result = tool.execute(path="f.txt", content="new", overwrite=True)
        assert result.success
        assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "new"
        assert result.output["mode"] == "overwrite"
        assert result.output["existed"] is True

    def test_append(self, tmp_path):
        tool = FileWriteTool(workspace_root=tmp_path)
        (tmp_path / "f.txt").write_text("hello ", encoding="utf-8")
        result = tool.execute(path="f.txt", content="world", append=True)
        assert result.success
        assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "hello world"

    def test_overwrite_and_append_mutually_exclusive(self, tmp_path):
        tool = FileWriteTool(workspace_root=tmp_path)
        result = tool.execute(
            path="f.txt", content="x",
            overwrite=True, append=True,
        )
        assert not result.success


# ════════════════════════════════════════════════════════════════════
# Sandboxing
# ════════════════════════════════════════════════════════════════════

class TestSandbox:

    def test_blocks_path_traversal(self, tmp_path):
        tool = FileWriteTool(workspace_root=tmp_path / "ws")
        result = tool.execute(path="../escape.txt", content="leak")
        assert not result.success
        assert "escape" in result.error.lower() or "workspace" in result.error.lower()

    def test_blocks_absolute_path(self, tmp_path):
        tool = FileWriteTool(workspace_root=tmp_path / "ws")
        result = tool.execute(path=str(tmp_path / "leak.txt"), content="x")
        assert not result.success
        assert "absolute" in result.error.lower() or "outside" in result.error.lower()

    def test_allow_outside_workspace_flag(self, tmp_path):
        tool = FileWriteTool(workspace_root=tmp_path / "ws")
        target = tmp_path / "outside.txt"
        result = tool.execute(
            path=str(target), content="ok",
            allow_outside_workspace=True,
        )
        assert result.success
        assert target.read_text(encoding="utf-8") == "ok"
        assert result.output["outside_workspace"] is True


# ════════════════════════════════════════════════════════════════════
# Limits + validation
# ════════════════════════════════════════════════════════════════════

class TestLimits:

    def test_size_cap(self, tmp_path, monkeypatch):
        from tools.builtins import file_write
        monkeypatch.setattr(file_write, "_MAX_BYTES", 100)
        tool = FileWriteTool(workspace_root=tmp_path)
        result = tool.execute(path="big.txt", content="x" * 1000)
        assert not result.success

    def test_content_must_be_string(self, tmp_path):
        tool = FileWriteTool(workspace_root=tmp_path)
        result = tool.execute(path="x.txt", content=123)
        assert not result.success

    def test_missing_path(self, tmp_path):
        tool = FileWriteTool(workspace_root=tmp_path)
        result = tool.execute(content="x")
        assert not result.success
