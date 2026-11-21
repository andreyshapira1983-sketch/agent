"""Unit tests for ListDirTool."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.list_dir import MAX_ENTRIES, ListDirTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    return tmp_path


def tool(ws: Path) -> ListDirTool:
    return ListDirTool(workspace_root=ws)


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
    def test_non_string_invalid(self, ws: Path):
        t = tool(ws)
        ok, errors = t.validate_output(42)
        assert not ok
        assert errors
