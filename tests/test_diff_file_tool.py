"""MVP-13.1 — unit tests for the `diff_file` tool."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tools.diff_file import (
    DEFAULT_CONTEXT_LINES,
    DEFAULT_MAX_BYTES,
    MAX_CONTEXT_LINES,
    MAX_DIFF_CHARS,
    DiffFileTool,
)


# ============================================================
# Construction
# ============================================================

class TestConstruction:
    def test_rejects_nonexistent_workspace(self, tmp_path: Path):
        with pytest.raises(ValueError, match="existing directory"):
            DiffFileTool(workspace_root=tmp_path / "nope")

    def test_rejects_non_positive_max_bytes(self, workspace: Path):
        with pytest.raises(ValueError, match="max_bytes"):
            DiffFileTool(workspace_root=workspace, max_bytes=0)
        with pytest.raises(ValueError):
            DiffFileTool(workspace_root=workspace, max_bytes=-1)

    def test_risk_is_read_only(self, workspace: Path):
        t = DiffFileTool(workspace_root=workspace)
        assert t.risk == "read_only"
        assert t.risk_for({}) == "read_only"


# ============================================================
# Argument validation
# ============================================================

class TestArgValidation:
    def test_empty_path_rejected(self, workspace: Path):
        t = DiffFileTool(workspace_root=workspace)
        with pytest.raises(ValueError, match="non-empty"):
            t.run(path="", proposed_content="")

    def test_non_string_path_rejected(self, workspace: Path):
        t = DiffFileTool(workspace_root=workspace)
        with pytest.raises(ValueError):
            t.run(path=123, proposed_content="")  # type: ignore[arg-type]

    def test_non_ascii_path_rejected(self, workspace: Path):
        t = DiffFileTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match="ASCII"):
            t.run(path="привет.txt", proposed_content="x")

    def test_non_string_content_rejected(self, workspace: Path):
        t = DiffFileTool(workspace_root=workspace)
        with pytest.raises(ValueError, match="must be a string"):
            t.run(path="a.txt", proposed_content=42)  # type: ignore[arg-type]

    def test_oversize_content_rejected(self, workspace: Path):
        t = DiffFileTool(workspace_root=workspace, max_bytes=100)
        big = "a" * 200
        with pytest.raises(ValueError, match="too large"):
            t.run(path="a.txt", proposed_content=big)

    def test_context_lines_negative_rejected(self, workspace: Path):
        t = DiffFileTool(workspace_root=workspace)
        with pytest.raises(ValueError, match=">= 0"):
            t.run(path="a.txt", proposed_content="x", context_lines=-1)

    def test_context_lines_too_large_rejected(self, workspace: Path):
        t = DiffFileTool(workspace_root=workspace)
        with pytest.raises(ValueError, match=f"<= {MAX_CONTEXT_LINES}"):
            t.run(path="a.txt", proposed_content="x",
                  context_lines=MAX_CONTEXT_LINES + 1)

    def test_non_int_context_lines_rejected(self, workspace: Path):
        t = DiffFileTool(workspace_root=workspace)
        with pytest.raises(ValueError, match="context_lines must be an int"):
            t.run(path="a.txt", proposed_content="x", context_lines=1.5)  # type: ignore[arg-type]


# ============================================================
# Sandbox
# ============================================================

class TestSandbox:
    def test_absolute_path_outside_workspace_rejected(self, workspace: Path):
        t = DiffFileTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match="outside workspace"):
            t.run(path="/etc/passwd", proposed_content="x")

    def test_relative_traversal_rejected(self, workspace: Path):
        t = DiffFileTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match="outside workspace"):
            t.run(path="../../etc/passwd", proposed_content="x")


# ============================================================
# Reading: file does not exist (= "create new")
# ============================================================

class TestNewFile:
    def test_diff_against_missing_file(self, workspace: Path):
        t = DiffFileTool(workspace_root=workspace)
        out = t.run(path="brand_new.txt",
                    proposed_content="hello\nworld\n")
        assert out["file_exists"] is False
        assert out["current_bytes"] == 0
        assert out["proposed_bytes"] == len("hello\nworld\n".encode("utf-8"))
        assert out["additions"] == 2  # two new lines
        assert out["deletions"] == 0
        assert "hello" in out["diff"]
        # Header lines should be present.
        assert out["diff"].startswith("---") or "+++" in out["diff"]


# ============================================================
# Reading: existing file
# ============================================================

class TestExistingFile:
    def test_no_changes_yields_empty_diff(self, workspace: Path):
        target = workspace / "same.txt"
        target.write_text("line1\nline2\n", encoding="utf-8")
        out = DiffFileTool(workspace_root=workspace).run(
            path="same.txt", proposed_content="line1\nline2\n"
        )
        assert out["file_exists"] is True
        assert out["diff"] == ""
        assert out["additions"] == 0
        assert out["deletions"] == 0

    def test_single_line_changed(self, workspace: Path):
        target = workspace / "mod.txt"
        target.write_text("alpha\nbravo\ncharlie\n", encoding="utf-8")
        out = DiffFileTool(workspace_root=workspace).run(
            path="mod.txt",
            proposed_content="alpha\nBRAVO\ncharlie\n",
        )
        assert out["additions"] == 1
        assert out["deletions"] == 1
        assert "BRAVO" in out["diff"]
        assert "-bravo" in out["diff"]

    def test_context_lines_argument_passed_through(self, workspace: Path):
        target = workspace / "ctx.txt"
        target.write_text(
            "\n".join(f"line{i}" for i in range(20)) + "\n",
            encoding="utf-8",
        )
        proposed = target.read_text(encoding="utf-8").replace(
            "line10", "LINE10"
        )
        out_small = DiffFileTool(workspace_root=workspace).run(
            path="ctx.txt", proposed_content=proposed, context_lines=0
        )
        out_big = DiffFileTool(workspace_root=workspace).run(
            path="ctx.txt", proposed_content=proposed, context_lines=5
        )
        # More context = longer diff.
        assert len(out_big["diff"]) > len(out_small["diff"])

    def test_oversize_existing_file_refused(self, workspace: Path):
        target = workspace / "big.txt"
        target.write_text("x" * 500, encoding="utf-8")
        t = DiffFileTool(workspace_root=workspace, max_bytes=100)
        with pytest.raises(ValueError, match="too large"):
            t.run(path="big.txt", proposed_content="")


# ============================================================
# Truncation
# ============================================================

class TestTruncation:
    def test_huge_diff_is_truncated(self, workspace: Path):
        target = workspace / "huge.txt"
        # Build a content that produces a very long diff.
        a = "\n".join(f"line{i}" for i in range(10000)) + "\n"
        b = "\n".join(f"LINE{i}" for i in range(10000)) + "\n"
        target.write_text(a, encoding="utf-8")
        out = DiffFileTool(workspace_root=workspace).run(
            path="huge.txt", proposed_content=b, context_lines=3
        )
        assert out["diff_truncated"] is True
        assert len(out["diff"]) <= MAX_DIFF_CHARS + 100  # +marker tail
        assert "<diff truncated>" in out["diff"]


# ============================================================
# Redaction
# ============================================================

class TestRedaction:
    def test_secret_in_proposed_content_redacted_in_diff(self, workspace: Path):
        t = DiffFileTool(workspace_root=workspace)
        secret = "sk-" + "B" * 48
        out = t.run(
            path="new.txt",
            proposed_content=f"api_key={secret}\n",
        )
        assert secret not in out["diff"]


# ============================================================
# validate_output
# ============================================================

class TestValidateOutput:
    def _ok(self) -> dict[str, Any]:
        return {
            "path": "a.txt",
            "file_exists": False,
            "current_bytes": 0,
            "proposed_bytes": 5,
            "additions": 1,
            "deletions": 0,
            "diff": "diff",
            "diff_truncated": False,
            "compensation_plan": {"id": "x", "actions": [], "tool_name": "diff_file", "description": "d"},
        }

    def test_well_formed_passes(self, workspace: Path):
        ok, _ = DiffFileTool(workspace_root=workspace).validate_output(self._ok())
        assert ok

    def test_non_dict_rejected(self, workspace: Path):
        ok, _ = DiffFileTool(workspace_root=workspace).validate_output("nope")
        assert not ok

    def test_missing_keys_rejected(self, workspace: Path):
        out = self._ok()
        del out["additions"]
        ok, _ = DiffFileTool(workspace_root=workspace).validate_output(out)
        assert not ok

    def test_negative_counts_rejected(self, workspace: Path):
        out = self._ok()
        out["additions"] = -1
        ok, _ = DiffFileTool(workspace_root=workspace).validate_output(out)
        assert not ok

    def test_non_bool_file_exists_rejected(self, workspace: Path):
        out = self._ok()
        out["file_exists"] = "yes"  # type: ignore[assignment]
        ok, _ = DiffFileTool(workspace_root=workspace).validate_output(out)
        assert not ok

    def test_non_string_diff_rejected(self, workspace: Path):
        out = self._ok()
        out["diff"] = ["+a"]  # type: ignore[assignment]
        ok, _ = DiffFileTool(workspace_root=workspace).validate_output(out)
        assert not ok


# ============================================================
# Constants pinned
# ============================================================

class TestConstants:
    def test_default_context_lines(self):
        assert DEFAULT_CONTEXT_LINES == 3

    def test_default_max_bytes(self):
        assert DEFAULT_MAX_BYTES == 1024 * 1024
