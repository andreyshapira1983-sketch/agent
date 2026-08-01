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

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from tools.shell_exec import (
    AGENT_BRANCH_PREFIX,
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
            # Env is still stripped down — no dotenv leaks, no PYTHONPATH
            # overrides. The home variables joined the allowed set so `git
            # commit` can find who is committing; they name a directory and
            # carry no credential. Nothing else may appear here.
            assert set(kwargs["env"].keys()) <= {
                "PATH", "SystemRoot", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
            }

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


# ===========================================================
# 9. Extended whitelist (git subcommands + findstr/grep)
# ===========================================================

class TestExtendedWhitelist:
    """Read-only commands added to unblock common diagnostics:
    git log/diff/status/show/branch/tag/blame and findstr/grep.
    """

    def _tool(self, workspace: Path) -> ShellExecTool:
        return ShellExecTool(workspace_root=workspace)

    # ---- risk classification ----
    def test_git_is_read_only(self, workspace: Path):
        tool = self._tool(workspace)
        assert tool.risk_for({"argv": ["git", "log"]}) == "read_only"
        assert tool.risk_for({"argv": ["git", "status"]}) == "read_only"

    def test_findstr_is_read_only(self, workspace: Path):
        tool = self._tool(workspace)
        assert tool.risk_for({"argv": ["findstr", "TODO"]}) == "read_only"
        assert tool.risk_for({"argv": ["grep", "-n", "TODO"]}) == "read_only"

    # ---- git subcommand whitelist ----
    @pytest.mark.parametrize("sub", [
        "log", "diff", "status", "show", "branch", "tag",
        "blame", "rev-parse", "describe", "ls-files", "ls-tree",
        "cat-file", "shortlog", "reflog", "name-rev",
    ])
    def test_git_safe_subcommands_validated(self, workspace: Path, sub: str):
        # Validation must pass; we don't actually need to execute git here.
        # _validate_argv is invoked by run() and raises only on rejection.
        tool = self._tool(workspace)
        cmd, argv = tool._validate_argv(["git", sub])
        assert cmd == "git"
        assert argv[1] == sub

    @pytest.mark.parametrize("sub", [
        "push", "pull", "fetch", "clone", "rm", "reset",
        "merge", "rebase", "stash", "config", "init",
        "mv", "restore", "switch", "clean", "gc",
    ])
    def test_git_dangerous_subcommands_rejected(self, workspace: Path, sub: str):
        with pytest.raises(PermissionError, match="subcommand not in"):
            self._tool(workspace).run(["git", sub])

    @pytest.mark.parametrize("sub", ["add", "commit", "checkout"])
    def test_recording_subcommands_are_shape_checked_not_blanket_denied(
        self, workspace: Path, sub: str
    ):
        """`add`, `commit` and `checkout` moved out of the blanket denial.

        They used to be refused with the same message as `push` and `reset`,
        which meant a programming task could never reach its last step. They
        are permitted now in one shape each, still gated by the policy (their
        risk is `irreversible`) and still refused on a protected branch — see
        `TestGitRecordingSubcommands`. What must NOT come back is the old
        message: a bare subcommand now fails on its shape, not on the list.
        """
        with pytest.raises(PermissionError) as excinfo:
            self._tool(workspace).run(["git", sub])
        assert "subcommand not in" not in str(excinfo.value)

    def test_git_without_subcommand_rejected(self, workspace: Path):
        with pytest.raises(PermissionError, match="requires a subcommand"):
            self._tool(workspace).run(["git"])

    def test_git_subcommand_case_insensitive(self, workspace: Path):
        tool = self._tool(workspace)
        cmd, _ = tool._validate_argv(["git", "LOG"])
        assert cmd == "git"

    # ---- findstr / grep platform alias ----
    def test_findstr_grep_platform_alias(self, workspace: Path):
        tool = self._tool(workspace)
        if sys.platform == "win32":
            assert tool._platform_alias("grep") == "findstr"
            assert tool._platform_alias("findstr") == "findstr"
        else:
            assert tool._platform_alias("findstr") == "grep"
            assert tool._platform_alias("grep") == "grep"

    # ---- destructive shells still rejected ----
    @pytest.mark.parametrize("argv0", [
        "rm", "del", "Remove-Item", "rmdir", "format", "mv", "ren",
        "Set-Content", "Out-File", "curl", "wget", "powershell", "cmd",
    ])
    def test_destructive_commands_still_rejected(self, workspace: Path, argv0: str):
        with pytest.raises(PermissionError, match="not in whitelist"):
            self._tool(workspace).run([argv0, "foo"])

    # ---- metachar protection still applies to git/findstr ----
    def test_git_metachar_argv_rejected(self, workspace: Path):
        with pytest.raises(PermissionError, match="forbidden character"):
            self._tool(workspace).run(["git", "log", "; rm -rf /"])

    def test_findstr_metachar_argv_rejected(self, workspace: Path):
        with pytest.raises(PermissionError, match="forbidden character"):
            self._tool(workspace).run(["findstr", "TODO", "| del *"])

    # ---- end-to-end execution (only if git is on PATH) ----
    def test_git_status_executes(self, workspace: Path):
        if shutil.which("git") is None:
            pytest.skip("git not on PATH")
        # Initialise a tiny repo so `git status` returns 0.
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)  # nosec B603 — literal argv, test fixture
        result = self._tool(workspace).run(["git", "status", "--porcelain"])
        assert result["exit_code"] == 0
        assert result["timed_out"] is False

# ===========================================================
# 12. Recording work: git add / commit / checkout -b
# ===========================================================

class TestGitRecordingSubcommands:
    """The agent may record its own work, and only its own.

    Measured on a live decomposition run: the agent read the code, ran the
    baseline suite and then stopped, because nothing in its tool surface could
    commit. `git` was whitelisted for reading only, so the last step of a
    programming task was unreachable however well it planned.
    """

    def _tool(self, workspace: Path) -> ShellExecTool:
        return ShellExecTool(workspace_root=workspace)

    def _repo(self, workspace: Path, branch: str) -> ShellExecTool:
        for args in (
            ["init", "-q"],
            ["config", "user.email", "t@example.com"],
            ["config", "user.name", "t"],
            # The host may sign commits or point core.hooksPath somewhere;
            # either would fail this fixture under check=True and error every
            # test in the class during setup.
            ["config", "commit.gpgsign", "false"],
            ["commit", "--allow-empty", "-q", "--no-verify", "-m", "root"],
            ["checkout", "-q", "-b", branch],
        ):
            # `["git", *args]` rather than a prebuilt argv: the literal head
            # is what static analysis can see, and it matches how the rest of
            # the suite shells out (tests/test_cli.py).
            subprocess.run(["git", *args], cwd=workspace, check=True,  # nosec B603 — literal argv, test fixture
                           capture_output=True)
        return self._tool(workspace)

    def test_recording_subcommands_ask_before_they_run(self, workspace: Path):
        tool = self._tool(workspace)
        assert tool.risk_for({"argv": ["git", "status"]}) == "read_only"
        for argv in (
            ["git", "add", "a.py"],
            ["git", "commit", "-m", "msg"],
            ["git", "checkout", "-b", "agent/x"],
        ):
            assert tool.risk_for({"argv": argv}) == "irreversible", argv

    def test_the_agent_may_create_its_own_branch_only(self, workspace: Path):
        tool = self._repo(workspace, "agent/work")
        tool._validate_argv(["git", "checkout", "-b", "agent/next"])
        with pytest.raises(PermissionError, match="under 'agent/'"):
            tool._validate_argv(["git", "checkout", "-b", "hotfix"])
        with pytest.raises(PermissionError, match="may only create a branch"):
            tool._validate_argv(["git", "checkout", "main"])

    def test_work_is_recorded_on_an_agent_branch(self, workspace: Path):
        tool = self._repo(workspace, "agent/work")
        tool._validate_argv(["git", "add", "a.py"])
        tool._validate_argv(["git", "commit", "-m", "Record the work"])

    def test_only_a_branch_the_agent_created_may_receive_work(self, workspace: Path):
        """The stated rule is "a branch the agent made", not "not main".

        Enumerating forbidden names let the agent record onto any operator
        branch that simply was not called main — observed on a live run, where
        it staged onto the operator's own working branch.
        """
        tool = self._repo(workspace, "agent/work")
        subprocess.run(["git", "checkout", "-q", "-b", "feature/login"],  # nosec B603 — literal argv, test fixture
                       cwd=workspace, check=True, capture_output=True)
        for sub in ("add", "commit"):
            argv = (["git", "add", "a.py"] if sub == "add"
                    else ["git", "commit", "-m", "onto the operator branch"])
            with pytest.raises(PermissionError, match="records only on a branch it created"):
                tool._validate_argv(argv)

    def test_a_detached_head_is_refused(self, workspace: Path):
        """`git rev-parse --abbrev-ref HEAD` answers "HEAD" when detached.

        Readable, so the unknown-branch guard passes; not a branch, so the
        commit would be reachable from no ref and would survive only until the
        next gc.
        """
        tool = self._repo(workspace, "agent/work")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace,  # nosec B603 — literal argv, test fixture
                              check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "checkout", "-q", "--detach", head],  # nosec B603 — literal argv, test fixture
                       cwd=workspace, check=True, capture_output=True)
        with pytest.raises(PermissionError, match="HEAD is detached"):
            tool._validate_argv(["git", "commit", "-m", "nowhere"])

    def test_reading_the_branch_uses_the_same_sandbox_env(self, workspace: Path):
        """The internal git call must not be the one path that inherits os.environ."""
        tool = self._repo(workspace, "agent/work")
        with mock.patch("tools.shell_exec.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="agent/work")
            tool._current_branch()
        env = run.call_args.kwargs["env"]
        assert set(env) <= {"PATH", "SystemRoot", "HOME", "USERPROFILE",
                            "HOMEDRIVE", "HOMEPATH"}

    def test_a_protected_branch_is_refused(self, workspace: Path):
        tool = self._repo(workspace, "agent/work")
        for args, required in ((["checkout", "-q", "master"], False),
                               (["checkout", "-q", "-B", "main"], True)):
            subprocess.run(["git", *args], cwd=workspace, check=required,  # nosec B603 — literal argv, test fixture
                           capture_output=True)
        with pytest.raises(PermissionError, match="protected branch"):
            tool._validate_argv(["git", "commit", "-m", "onto main"])

    def test_an_unreadable_branch_is_refused(self, workspace: Path):
        # No repository here at all: the guard cannot see the branch, so it
        # must not assume a safe one.
        tool = self._tool(workspace)
        with pytest.raises(PermissionError, match="could not be read"):
            tool._validate_argv(["git", "commit", "-m", "nowhere"])

    def test_add_takes_paths_not_a_sweep(self, workspace: Path):
        tool = self._repo(workspace, "agent/work")
        with pytest.raises(PermissionError, match="paths only"):
            tool._validate_argv(["git", "add", "-A"])
        with pytest.raises(PermissionError, match="requires explicit paths"):
            tool._validate_argv(["git", "add"])
        with pytest.raises(PermissionError):
            tool._validate_argv(["git", "add", "../outside.py"])

    def test_add_refuses_a_directory_however_it_is_spelled(self, workspace: Path):
        """`git add .` is `-A` with one character fewer.

        `git add <dir>` stages the directory recursively, so refusing the flag
        while accepting the path forbids the spelling and permits the act.
        """
        tool = self._repo(workspace, "agent/work")
        (workspace / "sub").mkdir()
        (workspace / "sub" / "x.py").write_text("x = 1", encoding="utf-8")
        for path in (".", "sub"):
            with pytest.raises(PermissionError, match="refuses the directory"):
                tool._validate_argv(["git", "add", path])
        tool._validate_argv(["git", "add", "sub/x.py"])

    def test_commit_shape_is_pinned(self, workspace: Path):
        tool = self._repo(workspace, "agent/work")
        for argv in (
            ["git", "commit"],
            ["git", "commit", "-m"],
            ["git", "commit", "-m", "   "],
            ["git", "commit", "--amend", "-m", "x"],
            ["git", "commit", "-m", "x", "--no-verify"],
        ):
            with pytest.raises(PermissionError, match="accepts exactly"):
                tool._validate_argv(argv)

    def test_branch_and_tag_may_only_list(self, workspace: Path):
        """They were read-only in name only.

        `_validate_argv` checked argv[1] alone, so `git branch -f main HEAD`
        moved a protected ref, `git branch -D` and `git tag -d` deleted one —
        and all three classified `read_only`, so the approval gate never saw
        them. Observed live: the agent created a branch with
        `git branch <name>` while the tool believed it was reading.
        """
        tool = self._repo(workspace, "agent/work")
        for argv in (
            ["git", "branch", "-f", "main", "HEAD"],
            ["git", "branch", "-D", "agent/work"],
            ["git", "branch", "-m", "renamed"],
            ["git", "tag", "-d", "v1"],
            ["git", "tag", "v1"],
        ):
            with pytest.raises(PermissionError):
                tool._validate_argv(argv)
        with pytest.raises(PermissionError, match="takes no name"):
            tool._validate_argv(["git", "branch", "newbranch"])
        # Listing still works, and still without approval.
        for argv in (["git", "branch"], ["git", "branch", "-a"],
                     ["git", "tag"], ["git", "tag", "-l"]):
            tool._validate_argv(argv)
            assert tool.risk_for({"argv": argv}) == "read_only"

    def test_history_and_network_stay_out(self, workspace: Path):
        tool = self._repo(workspace, "agent/work")
        for argv in (
            ["git", "push"],
            ["git", "pull"],
            ["git", "fetch"],
            ["git", "reset", "--hard"],
            ["git", "rebase", "main"],
            ["git", "merge", "main"],
        ):
            with pytest.raises(PermissionError, match="not in"):
                tool._validate_argv(argv)

    def test_a_commit_is_not_filed_as_nothing_to_undo(self, workspace: Path):
        tool = self._repo(workspace, "agent/work")
        plan = tool._build_compensation_plan("git", ["git", "commit", "-m", "x"], False)
        assert "nothing to undo" not in plan.description
        assert "not auto-undone" in plan.description

    def test_the_description_states_the_permissions_it_enforces(self, workspace: Path):
        """What the tool says is what the planner believes it may do.

        Measured on a live run: the permissions grew, this text did not, and
        the planner read the stale list, concluded it could not commit, and
        reported a failure it had never attempted. Its own reasoning quoted the
        old subcommand list back verbatim.
        """
        text = ShellExecTool(workspace_root=workspace).description.lower()
        for allowed in ("git", "add", "commit", "checkout", AGENT_BRANCH_PREFIX):
            assert allowed.lower() in text, allowed
        for refused in ("push", "reset", "rebase"):
            assert refused in text, refused

    def test_the_planner_prompt_and_the_tool_agree(self, workspace: Path):
        """One whitelist, two copies — they drifted the moment one changed.

        The planner does not read `ShellExecTool.description`; the catalogue in
        `core/planner.py` restates the whitelist. Measured on a live run: the
        permissions grew, that copy did not, and the planner refused to even
        attempt a commit, quoting the stale list back verbatim in its own
        reasoning. Twice — the second time after the tool's own description had
        already been corrected.
        """
        from core.planner import PLANNER_SYSTEM
        from tools.shell_exec import WRITE_SUBCOMMANDS

        prompt = PLANNER_SYSTEM.lower()
        for sub in WRITE_SUBCOMMANDS["git"]:
            assert f"git {sub}" in prompt or f'"{sub}"' in prompt, sub
        assert AGENT_BRANCH_PREFIX in prompt
        for refused in ("push", "reset", "rebase"):
            assert refused in prompt, refused

    def test_the_plan_sanitizer_keeps_the_steps_the_tool_allows(self, workspace: Path):
        """The third copy of the whitelist, and the one that actually bit.

        Measured live: the planner planned `git checkout -b agent/…`, `git add`
        and `git commit` correctly, and this sanitizer deleted all three as
        "subcommand not in […]" because it consulted the read-only half alone.
        The run then reported it could not commit. A gate that refuses a
        permission the tool grants is worse than one that never granted it: the
        capability exists, and only the plan knows it was taken away.
        """
        from core.planner import LLMPlanner

        warnings: list[str] = []
        for argv in (
            ["git", "checkout", "-b", "agent/x"],
            ["git", "add", "core/x.py"],
            ["git", "commit", "-m", "Record the work"],
        ):
            step = LLMPlanner._sanitize_step(
                "shell_exec", {"argv": argv}, None, 1, warnings
            )
            assert step is not None, f"{argv} was dropped: {warnings}"
        assert warnings == []

    def test_git_can_find_who_is_committing(self, workspace: Path):
        """The sandbox passed PATH only, so git had no identity.

        Measured on a live run: the agent wrote the files, ran the tests and
        staged them, and `git commit` then died on "Author identity unknown" —
        the last step of the last wall, caused by an env var, not a policy.
        These four name a directory and carry no credential.
        """
        env = ShellExecTool(workspace_root=workspace)._safe_env()
        assert "PATH" in env
        present = [n for n in ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH")
                   if os.environ.get(n)]
        if not present:
            pytest.skip("this runner defines no home variable to forward")
        for name in present:
            assert env.get(name) == os.environ[name], name
        # Everything else still stays out.
        assert "ANTHROPIC_API_KEY" not in env
        assert "OPENAI_API_KEY" not in env
