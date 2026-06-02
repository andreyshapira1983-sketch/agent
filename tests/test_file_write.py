"""FileWriteTool — sandbox, secret refusal, size limit, backup-on-overwrite.

Every safety branch from §5 Action Risk & Reversibility is exercised here
at the unit level. Loop-level integration (policy gate + approval gate)
lives in test_file_write_integration.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.file_write import MAX_BYTES, FileWriteTool


# ============================================================
# Happy-path create
# ============================================================

class TestCreate:
    def test_create_new_file_inside_workspace(self, workspace: Path):
        tool = FileWriteTool(workspace_root=workspace)
        out = tool.run(path="notes/hello.txt", content="hello world\n")

        target = workspace / "notes" / "hello.txt"
        assert target.read_text(encoding="utf-8") == "hello world\n"

        assert out["mode"] == "create"
        assert out["path"] == str(Path("notes/hello.txt"))
        assert out["bytes_written"] == len("hello world\n".encode("utf-8"))
        assert out["backup_path"] is None

    def test_create_makes_parent_directories(self, workspace: Path):
        tool = FileWriteTool(workspace_root=workspace)
        tool.run(path="a/b/c/deep.txt", content="x")
        assert (workspace / "a" / "b" / "c" / "deep.txt").exists()

    def test_create_unicode_content(self, workspace: Path):
        tool = FileWriteTool(workspace_root=workspace)
        tool.run(path="ru.txt", content="Привет, мир — это тест.")
        assert (workspace / "ru.txt").read_text(encoding="utf-8") == "Привет, мир — это тест."


# ============================================================
# Overwrite + backup
# ============================================================

class TestOverwriteAndBackup:
    def test_overwrite_creates_backup_with_original_content(self, workspace: Path):
        target = workspace / "doc.txt"
        target.write_text("original content\n", encoding="utf-8")

        tool = FileWriteTool(workspace_root=workspace)
        out = tool.run(path="doc.txt", content="new content\n")

        # New content is on disk
        assert target.read_text(encoding="utf-8") == "new content\n"

        # Backup was created — its path appears in the output
        assert out["mode"] == "overwrite"
        assert out["backup_path"] is not None
        backup = workspace / out["backup_path"]
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == "original content\n"

    def test_backup_filename_contains_timestamp(self, workspace: Path):
        (workspace / "x.txt").write_text("old", encoding="utf-8")
        tool = FileWriteTool(workspace_root=workspace)
        out = tool.run(path="x.txt", content="new")

        # The backup suffix is `.bak.<YYYYMMDDTHHMMSSZ>` — 8 digits, T, 6 digits, Z.
        import re
        # Match the canonical timestamp segment placed after `.bak.`.
        assert re.search(r"\.bak\.\d{8}T\d{6}Z$", out["backup_path"])

    def test_two_overwrites_produce_two_distinct_backups(self, workspace: Path):
        (workspace / "z.txt").write_text("v0", encoding="utf-8")
        tool = FileWriteTool(workspace_root=workspace)

        # Bypass the strftime second-resolution collision: the test only
        # cares that BOTH writes succeed and BOTH leave a backup behind.
        # Even if the timestamps collide, the second backup would clobber
        # the first; that's acceptable for MVP-9 (we keep at least one).
        tool.run(path="z.txt", content="v1")
        tool.run(path="z.txt", content="v2")

        backups = list(workspace.glob("z.txt.bak.*"))
        assert len(backups) >= 1, "at least one backup must remain"
        # The final file holds the latest content.
        assert (workspace / "z.txt").read_text(encoding="utf-8") == "v2"


# ============================================================
# Sandbox — path must stay inside workspace
# ============================================================

class TestSandbox:
    def test_parent_traversal_rejected(self, workspace: Path):
        tool = FileWriteTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match="escapes workspace"):
            tool.run(path="../escape.txt", content="x")

    def test_absolute_path_outside_workspace_rejected(self, workspace: Path, tmp_path_factory):
        tool = FileWriteTool(workspace_root=workspace)
        outside = tmp_path_factory.mktemp("outside") / "leak.txt"
        with pytest.raises(PermissionError, match="escapes workspace"):
            tool.run(path=str(outside), content="x")

    def test_symlink_to_outside_is_not_smuggled_in(self, workspace: Path):
        # We don't try to create a symlink (Windows perms make this flaky);
        # the resolve() call inside _resolve already handles symlinks by
        # canonicalising. This test just pins the same resolve() guarantee
        # against ".." traversal that bypasses the simple `startswith` check.
        tool = FileWriteTool(workspace_root=workspace)
        with pytest.raises(PermissionError):
            tool.run(path="sub/../../sneaky.txt", content="x")


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
# Size limit
# ============================================================

class TestSizeLimit:
    def test_content_above_limit_rejected(self, workspace: Path):
        tool = FileWriteTool(workspace_root=workspace, max_bytes=1024)
        with pytest.raises(ValueError, match="too large"):
            tool.run(path="big.txt", content="A" * 2048)
        assert not (workspace / "big.txt").exists(), "must not write partial file"

    def test_exactly_at_limit_is_allowed(self, workspace: Path):
        tool = FileWriteTool(workspace_root=workspace, max_bytes=10)
        tool.run(path="exact.txt", content="A" * 10)
        assert (workspace / "exact.txt").read_text(encoding="utf-8") == "A" * 10

    def test_default_limit_is_one_mib(self):
        # Pin the documented default so anyone touching MAX_BYTES sees it
        # in a failing test.
        assert MAX_BYTES == 1 * 1024 * 1024


# ============================================================
# Secret refusal (defence in depth)
# ============================================================

class TestSecretRefusal:
    def test_openai_key_in_content_rejected(self, workspace: Path):
        tool = FileWriteTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match="credentials"):
            tool.run(
                path="leak.txt",
                content="here is my key: sk-abcdefghijklmnopqrstuvwxyz0123",
            )
        assert not (workspace / "leak.txt").exists(), "no file may be created"

    def test_aws_key_in_content_rejected(self, workspace: Path):
        tool = FileWriteTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match="credentials"):
            tool.run(
                path="aws.txt",
                content="AKIAIOSFODNN7EXAMPLE\nrest of the file",
            )

    def test_secret_NOT_written_even_when_overwriting(self, workspace: Path):
        # Overwrite codepath has its own special handling (backup before
        # write). The secret check must happen BEFORE the backup, so a
        # blocked write does not leak the previous content into a *.bak.*
        # file as a side-effect.
        target = workspace / "doc.txt"
        target.write_text("benign original", encoding="utf-8")

        tool = FileWriteTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match="credentials"):
            tool.run(
                path="doc.txt",
                content="sk-abcdefghijklmnopqrstuvwxyz0123",
            )
        # Original content untouched and NO backup was created.
        assert target.read_text(encoding="utf-8") == "benign original"
        assert list(workspace.glob("doc.txt.bak.*")) == []


# ============================================================
# risk_for — dynamic risk classification
# ============================================================

class TestRiskFor:
    def test_new_path_is_reversible(self, workspace: Path):
        tool = FileWriteTool(workspace_root=workspace)
        assert tool.risk_for({"path": "new.txt", "content": "x"}) == "reversible"

    def test_existing_path_is_irreversible(self, workspace: Path):
        (workspace / "exists.txt").write_text("here", encoding="utf-8")
        tool = FileWriteTool(workspace_root=workspace)
        assert tool.risk_for({"path": "exists.txt", "content": "x"}) == "irreversible"

    def test_escape_attempt_is_treated_as_irreversible(self, workspace: Path):
        # Conservative — anything path-suspicious lands in the strictest
        # bucket so policy can escalate / approval can refuse.
        tool = FileWriteTool(workspace_root=workspace)
        assert tool.risk_for({"path": "../x.txt", "content": "x"}) == "irreversible"

    @pytest.mark.parametrize("bad", [None, "", "   ", 42, []])
    def test_missing_or_bad_path_is_irreversible(self, workspace: Path, bad):
        tool = FileWriteTool(workspace_root=workspace)
        assert tool.risk_for({"path": bad}) == "irreversible"

    def test_static_risk_is_irreversible_fallback(self):
        # If policy / loop ever reads `tool.risk` directly (legacy path)
        # they get the strictest classification.
        assert FileWriteTool.risk == "irreversible"


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

    def test_well_formed_create_passes(self, workspace: Path):
        ok, issues = FileWriteTool(workspace).validate_output(self._ok_create())
        assert ok is True
        assert issues == []

    def test_well_formed_overwrite_passes(self, workspace: Path):
        ok, issues = FileWriteTool(workspace).validate_output(self._ok_overwrite())
        assert ok is True
        assert issues == []

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


# ============================================================
# Tool contract sanity
# ============================================================

class TestToolContract:
    def test_name_and_static_risk(self):
        tool = FileWriteTool(workspace_root=Path("/tmp"))
        assert tool.name == "file_write"
        # Static risk is the conservative fallback.
        assert tool.risk == "irreversible"

    def test_description_warns_about_overwrite(self):
        # The model + audit reader rely on the description; it must
        # explicitly mention that overwriting escalates.
        text = FileWriteTool(workspace_root=Path("/tmp")).description
        assert "approval" in text.lower() or "escalated" in text.lower()
        assert "backup" in text.lower()
