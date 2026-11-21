"""FileWriteTool — sandbox, secret refusal, size limit, backup-on-overwrite.

Every safety branch from §5 Action Risk & Reversibility is exercised here
at the unit level. Loop-level integration (policy gate + approval gate)
lives in test_file_write_integration.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.file_write import FileWriteTool

# ============================================================
# Argument validation
# ============================================================

class TestArgumentValidation:
    @pytest.mark.parametrize("bad_path", ["", "   ", None, 42, [1, 2]])
    def test_bad_path_rejected_before_io(self, workspace: Path, bad_path):
        tool = FileWriteTool(workspace_root=workspace)
        with pytest.raises(ValueError, match="non-empty string"):
            tool.run(path=bad_path, content="x")  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad_content", [None, 42, ["x"], b"bytes"])
    def test_non_string_content_rejected(self, workspace: Path, bad_content):
        tool = FileWriteTool(workspace_root=workspace)
        with pytest.raises(TypeError, match="content must be a string"):
            tool.run(path="ok.txt", content=bad_content)  # type: ignore[arg-type]


# ============================================================
# Unfilled placeholder refusal (defence in depth)
# ============================================================

class TestPlaceholderRefusal:
    @pytest.mark.parametrize(
        "placeholder",
        [
            "<to be synthesized from the three files only>",
            "  <to be synthesized from the three files>  ",
            "<fill in the summary here>",
            "<TODO>",
            "<placeholder>",
        ],
    )
    def test_unfilled_placeholder_rejected_before_io(self, workspace: Path, placeholder):
        tool = FileWriteTool(workspace_root=workspace)
        with pytest.raises(ValueError, match="unfilled placeholder"):
            tool.run(path="about_me.txt", content=placeholder)
        assert not (workspace / "about_me.txt").exists(), "must not create a stub file"


# ============================================================
# Size limit
# ============================================================

class TestSizeLimit:
    def test_content_above_limit_rejected(self, workspace: Path):
        tool = FileWriteTool(workspace_root=workspace, max_bytes=1024)
        with pytest.raises(ValueError, match="too large"):
            tool.run(path="big.txt", content="A" * 2048)
        assert not (workspace / "big.txt").exists(), "must not write partial file"


# ============================================================
# validate_output
# ============================================================

class TestValidateOutput:
    def _ok_create(self) -> dict:
        return {
            "path": "x.txt",
            "mode": "create",
            "bytes_written": 5,
            "backup_path": None,
            "compensation_plan": {
                "id": "comp_test",
                "tool_name": "file_write",
                "description": "undo creation of x.txt",
                "created_at": "2026-01-01T00:00:00+00:00",
                "actions": [
                    {
                        "kind": "delete_path_if_created",
                        "description": "delete x.txt",
                        "path": "x.txt",
                        "backup_path": None,
                    }
                ],
            },
        }

    def _ok_overwrite(self) -> dict:
        return {
            "path": "x.txt",
            "mode": "overwrite",
            "bytes_written": 5,
            "backup_path": "x.txt.bak.20260101T000000Z",
            "compensation_plan": {
                "id": "comp_test",
                "tool_name": "file_write",
                "description": "undo overwrite of x.txt",
                "created_at": "2026-01-01T00:00:00+00:00",
                "actions": [
                    {
                        "kind": "restore_from_backup",
                        "description": "restore x.txt from backup",
                        "path": "x.txt",
                        "backup_path": "x.txt.bak.20260101T000000Z",
                    }
                ],
            },
        }


    def test_non_dict_output_hard_fail(self, workspace: Path):
        ok, issues = FileWriteTool(workspace).validate_output("not a dict")
        assert ok is False
        assert any("expected dict" in i for i in issues)

    def test_invalid_mode_flagged(self, workspace: Path):
        bad = {**self._ok_create(), "mode": "append"}
        ok, issues = FileWriteTool(workspace).validate_output(bad)
        assert ok is False
        assert any("invalid mode" in i for i in issues)

    def test_overwrite_without_backup_flagged(self, workspace: Path):
        bad = {**self._ok_overwrite(), "backup_path": None}
        ok, issues = FileWriteTool(workspace).validate_output(bad)
        assert ok is False
        assert any("overwrite without backup_path" in i for i in issues)

    def test_create_with_backup_flagged(self, workspace: Path):
        bad = {**self._ok_create(), "backup_path": "x.bak"}
        ok, issues = FileWriteTool(workspace).validate_output(bad)
        assert ok is False
        assert any("create must not have a backup_path" in i for i in issues)

    def test_negative_bytes_flagged(self, workspace: Path):
        bad = {**self._ok_create(), "bytes_written": -1}
        ok, issues = FileWriteTool(workspace).validate_output(bad)
        assert ok is False
        assert any("non-negative" in i for i in issues)

    # --- MVP-11: compensation plan contract in validate_output ---

    def test_missing_compensation_plan_flagged(self, workspace: Path):
        bad = self._ok_create()
        del bad["compensation_plan"]
        ok, issues = FileWriteTool(workspace).validate_output(bad)
        assert ok is False
        assert any("missing or non-dict compensation_plan" in i for i in issues)

    def test_non_dict_compensation_plan_flagged(self, workspace: Path):
        bad = {**self._ok_create(), "compensation_plan": "not-a-dict"}
        ok, issues = FileWriteTool(workspace).validate_output(bad)
        assert ok is False
        assert any("non-dict compensation_plan" in i for i in issues)

    def test_compensation_plan_wrong_action_for_create_flagged(
        self, workspace: Path
    ):
        bad = self._ok_create()
        bad["compensation_plan"]["actions"][0]["kind"] = "restore_from_backup"
        ok, issues = FileWriteTool(workspace).validate_output(bad)
        assert ok is False
        assert any("delete_path_if_created" in i for i in issues)

    def test_compensation_plan_wrong_action_for_overwrite_flagged(
        self, workspace: Path
    ):
        bad = self._ok_overwrite()
        bad["compensation_plan"]["actions"][0]["kind"] = "delete_path_if_created"
        ok, issues = FileWriteTool(workspace).validate_output(bad)
        assert ok is False
        assert any("restore_from_backup" in i for i in issues)

    def test_compensation_plan_must_have_exactly_one_action(
        self, workspace: Path
    ):
        bad = self._ok_create()
        bad["compensation_plan"]["actions"] = []
        ok, issues = FileWriteTool(workspace).validate_output(bad)
        assert ok is False
        assert any("exactly one action" in i for i in issues)
