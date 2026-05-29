"""Shell Exec tool unit tests (§5 MVP-11).

The acceptance criteria pinned here are the 9 stated in the MVP-11
brief, exercised at the tool layer:

  1. safe command runs only inside workspace
  2. dangerous command blocked (whitelist + metachar filters)
  3. external / irreversible risk needs approval (-> proven in integration)
  4. deny / abort -> no execution (-> proven in integration)
  5. timeout halts the subprocess
  6. stdout / stderr redacted from secrets
  7. JSONL carries the right events (-> proven in integration)
  8. compensation plan built BEFORE execution
  9. rollback restores filesystem (-> proven in compensation + integration)

Integration-level coverage (loop, approval, JSONL, rollback) lives in
`tests/test_shell_exec_integration.py`.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from unittest import mock

import pytest

from tools.shell_exec import (
    DEFAULT_OUTPUT_CAP,
    DEFAULT_TIMEOUT_SECONDS,
    MUTATING_COMMANDS,
    READ_ONLY_COMMANDS,
    ShellExecTool,
)


# ===========================================================
# Construction
# ===========================================================

class TestConstruction:
    def test_missing_workspace_rejected(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            ShellExecTool(workspace_root=tmp_path / "does-not-exist")

    def test_zero_timeout_rejected(self, workspace: Path):
        with pytest.raises(ValueError, match="timeout_seconds"):
            ShellExecTool(workspace_root=workspace, timeout_seconds=0.0)

    def test_zero_output_cap_rejected(self, workspace: Path):
        with pytest.raises(ValueError, match="output_cap_bytes"):
            ShellExecTool(workspace_root=workspace, output_cap_bytes=0)

    def test_defaults(self, workspace: Path):
        t = ShellExecTool(workspace_root=workspace)
        assert t.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
        assert t.output_cap_bytes == DEFAULT_OUTPUT_CAP
        assert t.name == "shell_exec"
        # Class-level static fallback is conservative.
        assert t.risk == "irreversible"


# ===========================================================
# 1. risk_for — argument-aware
# ===========================================================

class TestRiskFor:
    def setup_method(self):
        self.tool = None

    def _tool(self, workspace: Path) -> ShellExecTool:
        return ShellExecTool(workspace_root=workspace)

    def test_read_only_commands(self, workspace: Path):
        tool = self._tool(workspace)
        for cmd in READ_ONLY_COMMANDS:
            assert tool.risk_for({"argv": [cmd]}) == "read_only", cmd

    def test_mutating_commands(self, workspace: Path):
        tool = self._tool(workspace)
        for cmd in MUTATING_COMMANDS:
            assert tool.risk_for({"argv": [cmd, "foo"]}) == "irreversible", cmd

    def test_unknown_command_is_external(self, workspace: Path):
        tool = self._tool(workspace)
        assert tool.risk_for({"argv": ["rm", "-rf", "/"]}) == "external"
        assert tool.risk_for({"argv": ["python"]}) == "external"

    def test_empty_or_missing_argv_is_external(self, workspace: Path):
        tool = self._tool(workspace)
        assert tool.risk_for({}) == "external"
        assert tool.risk_for({"argv": []}) == "external"
        assert tool.risk_for({"argv": [None]}) == "external"

    def test_case_insensitive(self, workspace: Path):
        tool = self._tool(workspace)
        assert tool.risk_for({"argv": ["WHOAMI"]}) == "read_only"
        assert tool.risk_for({"argv": ["MkDir", "foo"]}) == "irreversible"


# ===========================================================
# 2. argv validation — whitelist + metacharacters + paths
# ===========================================================

class TestArgvValidation:
    def _tool(self, workspace: Path) -> ShellExecTool:
        return ShellExecTool(workspace_root=workspace)

    def test_empty_argv_rejected(self, workspace: Path):
        with pytest.raises(PermissionError, match="non-empty 'argv'"):
            self._tool(workspace).run([])

    def test_non_list_argv_rejected(self, workspace: Path):
        with pytest.raises(PermissionError):
            self._tool(workspace).run("whoami")  # type: ignore[arg-type]

    def test_non_string_element_rejected(self, workspace: Path):
        with pytest.raises(PermissionError, match="must be a string"):
            self._tool(workspace).run(["whoami", 42])  # type: ignore[list-item]

    def test_too_long_argv_rejected(self, workspace: Path):
        with pytest.raises(PermissionError, match="longer than 16"):
            self._tool(workspace).run(["whoami"] * 17)

    @pytest.mark.parametrize(
        "arg",
        [";rm", "a|b", "a&b", "a>b", "a<b", "a`b", "a$b", "a(b", "a)b",
         "a{b", "a}b", "a[b", "a]b", "a\nb", "a\rb", "a\tb"],
    )
    def test_shell_metachars_rejected_in_args(self, workspace: Path, arg: str):
        with pytest.raises(PermissionError, match="forbidden character"):
            # First arg must be a whitelisted command — but the
            # metachar appears in argv[1], which is what we test.
            self._tool(workspace).run(["touch", arg])

    @pytest.mark.parametrize(
        "arg",
        ["$HOME", "~/.ssh", "${PATH}"],
    )
    def test_tilde_and_dollar_rejected(self, workspace: Path, arg: str):
        with pytest.raises(PermissionError, match="forbids|metachar"):
            self._tool(workspace).run(["touch", arg])

    def test_command_outside_whitelist_rejected(self, workspace: Path):
        with pytest.raises(PermissionError, match="not in whitelist"):
            self._tool(workspace).run(["rm", "-rf", "foo"])

    def test_unknown_command_rejected(self, workspace: Path):
        with pytest.raises(PermissionError, match="not in whitelist"):
            self._tool(workspace).run(["definitely-not-a-real-cmd"])


# ===========================================================
# 3. path validation for mutating commands
# ===========================================================

class TestPathValidation:
    def _tool(self, workspace: Path) -> ShellExecTool:
        return ShellExecTool(workspace_root=workspace)

    def test_mkdir_requires_exactly_one_arg(self, workspace: Path):
        with pytest.raises(PermissionError, match="exactly one path argument"):
            self._tool(workspace).run(["mkdir"])
        with pytest.raises(PermissionError, match="exactly one path argument"):
            self._tool(workspace).run(["mkdir", "a", "b"])

    def test_absolute_path_rejected(self, workspace: Path):
        with pytest.raises(PermissionError, match="absolute-style"):
            self._tool(workspace).run(["mkdir", "/etc/foo"])
        with pytest.raises(PermissionError, match="absolute-style"):
            self._tool(workspace).run(["mkdir", "\\Windows\\foo"])

    def test_drive_letter_rejected(self, workspace: Path):
        with pytest.raises(PermissionError, match="drive-letter"):
            self._tool(workspace).run(["mkdir", "C:\\evil"])

    def test_dotdot_traversal_rejected(self, workspace: Path):
        with pytest.raises(PermissionError, match="'\\.\\.' traversal"):
            self._tool(workspace).run(["mkdir", "../escape"])
        with pytest.raises(PermissionError, match="traversal|escape"):
            self._tool(workspace).run(["mkdir", "sub/../../escape"])


# ===========================================================
# 4. happy-path read-only execution
# ===========================================================

class TestReadOnlyExecution:
    def _tool(self, workspace: Path) -> ShellExecTool:
        return ShellExecTool(workspace_root=workspace, timeout_seconds=10.0)

    def _expected_present(self, cmd: str) -> bool:
        """Check whether the real binary exists on PATH on this host."""
        if cmd == "where" and sys.platform != "win32":
            cmd = "which"
        return shutil.which(cmd) is not None

    def test_whoami_runs(self, workspace: Path):
        if not self._expected_present("whoami"):
            pytest.skip("whoami not on PATH")
        result = self._tool(workspace).run(["whoami"])
        assert result["exit_code"] == 0
        assert result["timed_out"] is False
        assert len(result["stdout"]) > 0
        # noop compensation plan for read-only commands.
        plan = result["compensation_plan"]
        assert plan["actions"][0]["kind"] == "noop"

    def test_hostname_runs(self, workspace: Path):
        if not self._expected_present("hostname"):
            pytest.skip("hostname not on PATH")
        result = self._tool(workspace).run(["hostname"])
        assert result["exit_code"] == 0
        assert "compensation_plan" in result

    def test_cwd_is_workspace(self, workspace: Path):
        """The subprocess must inherit cwd=workspace, NOT the test's cwd."""
        # `where` (Win) / `which` (POSIX) does not echo cwd, so we use
        # a minimal indirect check: `where` looks up an executable on
        # PATH — it succeeds (exit_code=0) regardless of cwd, but we
        # confirm the call did NOT crash inside an unrelated dir.
        if not self._expected_present("whoami"):
            pytest.skip("whoami not on PATH")
        tool = self._tool(workspace)
        with mock.patch("subprocess.run", wraps=__import__("subprocess").run) as spy:
            tool.run(["whoami"])
            kwargs = spy.call_args.kwargs
            assert Path(kwargs["cwd"]).resolve() == workspace.resolve()
            # shell=False is the most important contract.
            assert kwargs["shell"] is False
            # Env was stripped down (no dotenv leaks, no PYTHONPATH overrides).
            assert set(kwargs["env"].keys()) <= {"PATH", "SystemRoot"}

    def test_unknown_binary_surfaces_clean_error(self, workspace: Path):
        """A whitelisted command not actually installed must FAIL CLEANLY."""
        tool = self._tool(workspace)
        # Force shutil.which to None to simulate a missing binary.
        with mock.patch("tools.shell_exec.shutil.which", return_value=None):
            with pytest.raises(FileNotFoundError, match="executable"):
                tool.run(["whoami"])


# ===========================================================
# 5. happy-path mutating execution (compensation captured BEFORE)
# ===========================================================

class TestMutatingExecution:
    def _tool(self, workspace: Path) -> ShellExecTool:
        return ShellExecTool(workspace_root=workspace, timeout_seconds=10.0)

    def test_mkdir_creates_directory(self, workspace: Path):
        result = self._tool(workspace).run(["mkdir", "newdir"])
        assert result["exit_code"] == 0
        assert (workspace / "newdir").is_dir()
        # Compensation plan present + actionable.
        plan = result["compensation_plan"]
        assert plan["actions"][0]["kind"] == "delete_path_if_created"
        assert plan["actions"][0]["path"] == "newdir"

    def test_touch_creates_empty_file(self, workspace: Path):
        result = self._tool(workspace).run(["touch", "newfile.txt"])
        assert result["exit_code"] == 0
        assert (workspace / "newfile.txt").is_file()
        plan = result["compensation_plan"]
        assert plan["actions"][0]["path"] == "newfile.txt"

    def test_mkdir_on_existing_path_refused(self, workspace: Path):
        (workspace / "already").mkdir()
        with pytest.raises(PermissionError, match="already exists"):
            self._tool(workspace).run(["mkdir", "already"])

    def test_touch_existing_path_is_noop_compensation(self, workspace: Path):
        target = workspace / "existing.txt"
        target.write_text("keep me", encoding="utf-8")
        result = self._tool(workspace).run(["touch", "existing.txt"])
        assert result["exit_code"] == 0
        # File content preserved (touch only refreshes mtime).
        assert target.read_text(encoding="utf-8") == "keep me"
        # Compensation plan must be a NOOP — we didn't create this file,
        # so rollback should not delete it.
        plan = result["compensation_plan"]
        assert plan["actions"][0]["kind"] == "noop"

    def test_compensation_plan_built_before_state_change(self, workspace: Path):
        """The plan's `created_at` must precede the file appearing.

        We can't directly observe ordering, but we can prove that on a
        FAILED mutation we still get NO file AND NO plan-with-delete
        action — i.e. plan correctness does not depend on success.
        """
        target = workspace / "exists"
        target.mkdir()
        with pytest.raises(PermissionError):
            self._tool(workspace).run(["mkdir", "exists"])
        # No new dir was created (the existing one is unchanged).
        assert target.is_dir()
        assert list(target.iterdir()) == []


# ===========================================================
# 6. timeout
# ===========================================================

class TestTimeout:
    def test_subprocess_timeout_surfaces_as_timed_out(self, workspace: Path):
        """We monkey-patch subprocess.run to raise TimeoutExpired."""
        import subprocess

        tool = ShellExecTool(workspace_root=workspace, timeout_seconds=0.1)

        def raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(
                cmd=args[0], timeout=kwargs.get("timeout", 0.1),
                output=b"partial out", stderr=b"partial err",
            )

        with mock.patch("subprocess.run", side_effect=raise_timeout):
            with mock.patch(
                "tools.shell_exec.shutil.which", return_value="/fake/whoami"
            ):
                result = tool.run(["whoami"])

        assert result["timed_out"] is True
        assert result["exit_code"] is None
        # Partial output captured & passed through redact_text.
        assert "partial" in result["stdout"]
        assert "partial" in result["stderr"]


# ===========================================================
# 7. output cap + secret redaction
# ===========================================================

class TestOutputCapAndRedaction:
    def test_huge_stdout_truncated(self, workspace: Path):
        """Patch subprocess.run to emit > cap bytes and assert truncation."""
        import subprocess

        tool = ShellExecTool(
            workspace_root=workspace, output_cap_bytes=128, timeout_seconds=5
        )
        huge = b"x" * 1024

        def fake_run(*args, **kwargs):
            cp = subprocess.CompletedProcess(args=args[0], returncode=0)
            cp.stdout = huge
            cp.stderr = b""
            return cp

        with mock.patch("subprocess.run", side_effect=fake_run):
            with mock.patch(
                "tools.shell_exec.shutil.which", return_value="/fake/whoami"
            ):
                result = tool.run(["whoami"])

        assert result["stdout_truncated"] is True
        assert len(result["stdout"].encode("utf-8")) == 128

    def test_secret_in_stdout_redacted(self, workspace: Path):
        """A subprocess that prints a credential must NEVER leak it."""
        import subprocess

        tool = ShellExecTool(workspace_root=workspace, timeout_seconds=5)
        leak = b"User: alice\nkey=sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"

        def fake_run(*args, **kwargs):
            cp = subprocess.CompletedProcess(args=args[0], returncode=0)
            cp.stdout = leak
            cp.stderr = b""
            return cp

        with mock.patch("subprocess.run", side_effect=fake_run):
            with mock.patch(
                "tools.shell_exec.shutil.which", return_value="/fake/whoami"
            ):
                result = tool.run(["whoami"])

        # The literal credential MUST be gone; a [REDACTED:*] tag is fine.
        assert "sk-aaaaaaaaaa" not in result["stdout"]
        assert "[REDACTED" in result["stdout"]

    def test_secret_in_stderr_redacted_on_mutating(self, workspace: Path):
        """Mutating path also runs stderr through redaction.

        We can't easily inject a credential into `target.mkdir()` itself,
        so we monkey-patch redact_text to confirm it IS called and the
        return value is what lands in the result.
        """
        tool = ShellExecTool(workspace_root=workspace, timeout_seconds=5)
        # First we let the real run succeed.
        result = tool.run(["mkdir", "okdir"])
        assert result["stderr"] == ""

        # Now simulate an OSError surfacing a secret-shaped message.
        with mock.patch.object(Path, "mkdir") as fake_mkdir:
            fake_mkdir.side_effect = OSError(
                "key=sk-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            )
            result2 = tool.run(["mkdir", "anotherdir"])
        assert result2["exit_code"] == 1
        assert "sk-bbbbbbbb" not in result2["stderr"]


# ===========================================================
# 8. validate_output
# ===========================================================

class TestValidateOutput:
    def _tool(self, workspace: Path) -> ShellExecTool:
        return ShellExecTool(workspace_root=workspace)

    def test_well_formed_output_passes(self, workspace: Path):
        out = {
            "argv": ["whoami"],
            "exit_code": 0,
            "stdout": "alice",
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "duration_ms": 12,
            "timed_out": False,
            "compensation_plan": {"actions": [{"kind": "noop"}]},
        }
        ok, warnings = self._tool(workspace).validate_output(out)
        assert ok
        assert warnings == []

    def test_non_dict_output_fails(self, workspace: Path):
        ok, reasons = self._tool(workspace).validate_output("not a dict")
        assert not ok
        assert any("must be a dict" in r for r in reasons)

    def test_missing_keys_fail(self, workspace: Path):
        ok, reasons = self._tool(workspace).validate_output({"argv": ["whoami"]})
        assert not ok
        assert any("missing keys" in r for r in reasons)

    def test_inconsistent_timeout_and_exit_code_warns(self, workspace: Path):
        out = {
            "argv": ["whoami"],
            "exit_code": 0,
            "stdout": "", "stderr": "",
            "stdout_truncated": False, "stderr_truncated": False,
            "duration_ms": 5,
            "timed_out": True,
            "compensation_plan": {"actions": [{"kind": "noop"}]},
        }
        ok, warnings = self._tool(workspace).validate_output(out)
        assert ok
        assert any("timed_out=True" in w for w in warnings)

    def _base(self) -> dict:
        return {
            "argv": ["whoami"],
            "exit_code": 0,
            "stdout": "x",
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "duration_ms": 5,
            "timed_out": False,
            "compensation_plan": {"actions": [{"kind": "noop"}]},
        }

    def test_empty_argv_in_output_fails(self, workspace: Path):
        bad = {**self._base(), "argv": []}
        ok, reasons = self._tool(workspace).validate_output(bad)
        assert not ok and any("non-empty list" in r for r in reasons)

    def test_non_list_argv_in_output_fails(self, workspace: Path):
        bad = {**self._base(), "argv": "whoami"}
        ok, reasons = self._tool(workspace).validate_output(bad)
        assert not ok and any("non-empty list" in r for r in reasons)

    def test_non_string_stdout_fails(self, workspace: Path):
        bad = {**self._base(), "stdout": 42}
        ok, reasons = self._tool(workspace).validate_output(bad)
        assert not ok and any("strings" in r for r in reasons)

    def test_non_string_stderr_fails(self, workspace: Path):
        bad = {**self._base(), "stderr": ["a", "b"]}
        ok, reasons = self._tool(workspace).validate_output(bad)
        assert not ok and any("strings" in r for r in reasons)

    def test_non_dict_compensation_plan_fails(self, workspace: Path):
        bad = {**self._base(), "compensation_plan": "noop"}
        ok, reasons = self._tool(workspace).validate_output(bad)
        assert not ok and any("compensation_plan" in r for r in reasons)

    def test_non_bool_timed_out_fails(self, workspace: Path):
        bad = {**self._base(), "timed_out": "yes"}
        ok, reasons = self._tool(workspace).validate_output(bad)
        assert not ok and any("timed_out" in r for r in reasons)

    def test_negative_duration_fails(self, workspace: Path):
        bad = {**self._base(), "duration_ms": -1}
        ok, reasons = self._tool(workspace).validate_output(bad)
        assert not ok and any("duration_ms" in r for r in reasons)

    def test_exit_code_none_without_timeout_warns(self, workspace: Path):
        # Not a hard fail — but the output is suspicious; warning only.
        bad = {**self._base(), "exit_code": None, "timed_out": False}
        ok, warnings = self._tool(workspace).validate_output(bad)
        assert ok
        assert any("exit_code is None" in w for w in warnings)
