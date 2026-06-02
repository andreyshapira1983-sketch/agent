"""Unit tests for ListDirTool."""
from __future__ import annotations

import pytest
from pathlib import Path

from tools.list_dir import ListDirTool, MAX_ENTRIES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    return tmp_path


def tool(ws: Path) -> ListDirTool:
    return ListDirTool(workspace_root=ws)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestBasicListing:
    def test_lists_files_in_root(self, ws: Path):
        (ws / "a.txt").write_text("a")
        (ws / "b.txt").write_text("b")
        t = tool(ws)
        result = t.run(".")
        assert "a.txt" in result
        assert "b.txt" in result

    def test_directories_end_with_slash(self, ws: Path):
        (ws / "subdir").mkdir()
        t = tool(ws)
        result = t.run(".")
        assert "subdir/" in result

    def test_empty_path_uses_root(self, ws: Path):
        (ws / "x.md").write_text("x")
        t = tool(ws)
        result = t.run("")
        assert "x.md" in result

    def test_subdirectory_listing(self, ws: Path):
        sub = ws / "mydir"
        sub.mkdir()
        (sub / "c.py").write_text("c")
        t = tool(ws)
        result = t.run("mydir")
        assert "c.py" in result

    def test_empty_directory_returns_marker(self, ws: Path):
        (ws / "empty").mkdir()
        t = tool(ws)
        result = t.run("empty")
        assert "(empty directory)" in result

    def test_dirs_sorted_before_files(self, ws: Path):
        (ws / "z_file.txt").write_text("z")
        (ws / "a_dir").mkdir()
        t = tool(ws)
        result = t.run(".")
        lines = result.splitlines()
        dir_idx = next(i for i, l in enumerate(lines) if l.endswith("/"))
        file_idx = next(i for i, l in enumerate(lines) if not l.endswith("/"))
        assert dir_idx < file_idx

    def test_windows_backslash_path(self, ws: Path):
        sub = ws / "docs"
        sub.mkdir()
        (sub / "readme.txt").write_text("hi")
        t = tool(ws)
        result = t.run(".\\docs")
        assert "readme.txt" in result


# ---------------------------------------------------------------------------
# Sandbox / security
# ---------------------------------------------------------------------------

class TestSandbox:
    def test_path_traversal_rejected(self, ws: Path):
        t = tool(ws)
        with pytest.raises(PermissionError, match="escapes workspace"):
            t.run("../")

    def test_absolute_path_outside_workspace_rejected(self, ws: Path, tmp_path: Path):
        outside = tmp_path.parent
        t = tool(ws)
        with pytest.raises(PermissionError, match="escapes workspace"):
            t.run(str(outside))

    def test_file_not_directory_raises(self, ws: Path):
        (ws / "file.txt").write_text("hello")
        t = tool(ws)
        with pytest.raises(NotADirectoryError):
            t.run("file.txt")

    def test_nonexistent_directory_raises(self, ws: Path):
        t = tool(ws)
        with pytest.raises(FileNotFoundError):
            t.run("does_not_exist")

    def test_non_string_path_raises(self, ws: Path):
        t = tool(ws)
        with pytest.raises(PermissionError):
            t.run(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

class TestTruncation:
    def test_truncation_at_max_entries(self, ws: Path):
        sub = ws / "many"
        sub.mkdir()
        for i in range(MAX_ENTRIES + 5):
            (sub / f"f{i:04d}.txt").write_text("")
        t = tool(ws)
        result = t.run("many")
        assert "truncated" in result
        assert str(MAX_ENTRIES) in result


# ---------------------------------------------------------------------------
# validate_output
# ---------------------------------------------------------------------------

class TestValidateOutput:
    def test_valid_string(self, ws: Path):
        t = tool(ws)
        ok, errors = t.validate_output("file.txt")
        assert ok
        assert errors == []

    def test_non_string_invalid(self, ws: Path):
        t = tool(ws)
        ok, errors = t.validate_output(42)
        assert not ok
        assert errors
