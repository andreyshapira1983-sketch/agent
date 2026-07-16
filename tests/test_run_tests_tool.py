"""MVP-13.1 — unit tests for the `run_tests` tool.

We don't actually invoke the real `pytest` here for most tests; we
swap `subprocess.run` with a stub that returns canned bytes. This
keeps the suite hermetic (no recursive pytest invocations) and lets
us pin EVERY corner of the contract.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from tools.run_tests import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_PATHS,
    MAX_PATTERN_LEN,
    RunTestsTool,
)


# ============================================================
# Helpers
# ============================================================

class _FakeCompleted:
    def __init__(self, *, returncode: int, stdout: bytes, stderr: bytes):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_run(monkeypatch, *, returncode=0, stdout=b"", stderr=b""):
    """Replace subprocess.run with a stub that captures argv + env."""
    captured: dict[str, Any] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return _FakeCompleted(
            returncode=returncode, stdout=stdout, stderr=stderr
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    return captured


# ============================================================
# Construction
# ============================================================

class TestConstruction:
    def test_rejects_nonexistent_workspace(self, tmp_path: Path):
        with pytest.raises(ValueError, match="existing directory"):
            RunTestsTool(workspace_root=tmp_path / "nope")

    def test_rejects_zero_timeout(self, workspace: Path):
        with pytest.raises(ValueError, match="timeout_seconds"):
            RunTestsTool(workspace_root=workspace, timeout_seconds=0)

    def test_rejects_negative_timeout(self, workspace: Path):
        with pytest.raises(ValueError):
            RunTestsTool(workspace_root=workspace, timeout_seconds=-1)

    def test_default_timeout_is_300s(self, workspace: Path):
        t = RunTestsTool(workspace_root=workspace)
        assert t.timeout_seconds == DEFAULT_TIMEOUT_SECONDS == 300.0

    def test_risk_is_reversible(self, workspace: Path):
        t = RunTestsTool(workspace_root=workspace)
        assert t.risk == "reversible"
        assert t.risk_for({}) == "reversible"
        assert t.risk_for({"paths": ["tests/"]}) == "reversible"


# ============================================================
# Argv construction & sandboxing
# ============================================================

class TestArgvSandbox:
    def test_default_argv_runs_tests_dir(self, workspace: Path, monkeypatch):
        captured = _patch_run(monkeypatch, stdout=b"1 passed in 0.01s\n")
        t = RunTestsTool(workspace_root=workspace)
        t.run()
        argv = captured["argv"]
        assert argv[1:5] == ["-m", "pytest", "-q", "--tb=short"]
        assert "tests" in argv          # default path
        assert "--no-header" in argv

    def test_paths_appended_after_flags(self, workspace: Path, monkeypatch):
        captured = _patch_run(monkeypatch, stdout=b"")
        t = RunTestsTool(workspace_root=workspace)
        t.run(paths=["tests/test_x.py", "tests/test_y.py"])
        argv = captured["argv"]
        assert "tests/test_x.py" in argv
        assert "tests/test_y.py" in argv

    def test_pattern_passed_as_dash_k(self, workspace: Path, monkeypatch):
        captured = _patch_run(monkeypatch, stdout=b"")
        t = RunTestsTool(workspace_root=workspace)
        t.run(pattern="memory")
        argv = captured["argv"]
        assert "-k" in argv
        assert argv[argv.index("-k") + 1] == "memory"

    def test_non_list_paths_rejected(self, workspace: Path):
        t = RunTestsTool(workspace_root=workspace)
        with pytest.raises(ValueError, match="paths must be a list"):
            t.run(paths="tests")  # type: ignore[arg-type]

    def test_too_many_paths_rejected(self, workspace: Path):
        t = RunTestsTool(workspace_root=workspace)
        many = [f"tests/t{i}.py" for i in range(MAX_PATHS + 1)]
        with pytest.raises(ValueError, match="too many paths"):
            t.run(paths=many)

    def test_path_with_parent_traversal_rejected(self, workspace: Path):
        t = RunTestsTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match=r"\.\."):
            t.run(paths=["../escape"])

    def test_absolute_path_rejected(self, workspace: Path):
        t = RunTestsTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match="outside workspace"):
            t.run(paths=["/etc/passwd"])

    def test_non_ascii_path_rejected(self, workspace: Path):
        t = RunTestsTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match="ASCII"):
            t.run(paths=["тесты/"])

    def test_non_string_pattern_rejected(self, workspace: Path):
        t = RunTestsTool(workspace_root=workspace)
        with pytest.raises(ValueError, match="pattern must be a string"):
            t.run(pattern=123)  # type: ignore[arg-type]

    def test_pattern_too_long_rejected(self, workspace: Path):
        t = RunTestsTool(workspace_root=workspace)
        with pytest.raises(ValueError, match="pattern too long"):
            t.run(pattern="x" * (MAX_PATTERN_LEN + 1))

    def test_non_ascii_pattern_rejected(self, workspace: Path):
        t = RunTestsTool(workspace_root=workspace)
        with pytest.raises(PermissionError, match="ASCII"):
            t.run(pattern="русский")


# ============================================================
# Subprocess invocation contract
# ============================================================

class TestSubprocessContract:
    def test_shell_false_always(self, workspace: Path, monkeypatch):
        captured = _patch_run(monkeypatch, stdout=b"")
        RunTestsTool(workspace_root=workspace).run()
        assert captured["kwargs"]["shell"] is False

    def test_cwd_is_workspace(self, workspace: Path, monkeypatch):
        captured = _patch_run(monkeypatch, stdout=b"")
        RunTestsTool(workspace_root=workspace).run()
        assert Path(captured["kwargs"]["cwd"]).resolve() == workspace.resolve()

    def test_timeout_passed_to_subprocess(self, workspace: Path, monkeypatch):
        captured = _patch_run(monkeypatch, stdout=b"")
        RunTestsTool(workspace_root=workspace, timeout_seconds=12.5).run()
        assert captured["kwargs"]["timeout"] == 12.5

    def test_capture_output_true(self, workspace: Path, monkeypatch):
        captured = _patch_run(monkeypatch, stdout=b"")
        RunTestsTool(workspace_root=workspace).run()
        assert captured["kwargs"]["capture_output"] is True

    def test_env_carries_pythonioencoding(self, workspace: Path, monkeypatch):
        captured = _patch_run(monkeypatch, stdout=b"")
        RunTestsTool(workspace_root=workspace).run()
        assert captured["kwargs"]["env"].get("PYTHONIOENCODING") == "utf-8"

    def test_env_excludes_sensitive_keys(self, workspace: Path, monkeypatch):
        """Don't carry arbitrary env vars into pytest — keep the allow-list short."""
        monkeypatch.setenv("MY_API_KEY", "secret-value")
        captured = _patch_run(monkeypatch, stdout=b"")
        RunTestsTool(workspace_root=workspace).run()
        assert "MY_API_KEY" not in captured["kwargs"]["env"]


# ============================================================
# Output parsing
# ============================================================

class TestParseCounts:
    def test_passed_only(self, workspace: Path, monkeypatch):
        _patch_run(monkeypatch, stdout=b"5 passed in 0.12s\n")
        out = RunTestsTool(workspace_root=workspace).run()
        assert out["passed"] == 5
        assert out["failed"] == 0
        assert out["total"] == 5

    def test_mixed_outcomes(self, workspace: Path, monkeypatch):
        _patch_run(
            monkeypatch,
            stdout=b"7 passed, 2 failed, 1 error, 3 skipped in 0.5s\n",
            returncode=1,
        )
        out = RunTestsTool(workspace_root=workspace).run()
        assert out["passed"] == 7
        assert out["failed"] == 2
        assert out["errors"] == 1
        assert out["skipped"] == 3
        assert out["total"] == 13
        assert out["exit_code"] == 1

    def test_failed_test_names_extracted(self, workspace: Path, monkeypatch):
        stdout = (
            b"========== short test summary info ==========\n"
            b"FAILED tests/test_x.py::TestA::test_one - AssertionError: nope\n"
            b"FAILED tests/test_y.py::test_two\n"
            b"ERROR tests/test_z.py::test_three - ImportError\n"
            b"2 failed, 1 error in 0.3s\n"
        )
        _patch_run(monkeypatch, stdout=stdout, returncode=1)
        out = RunTestsTool(workspace_root=workspace).run()
        assert out["failed_tests"] == [
            "tests/test_x.py::TestA::test_one",
            "tests/test_y.py::test_two",
            "tests/test_z.py::test_three",
        ]

    def test_no_summary_line_means_zero_counts(self, workspace: Path, monkeypatch):
        _patch_run(monkeypatch, stdout=b"some garbage\n", returncode=2)
        out = RunTestsTool(workspace_root=workspace).run()
        assert out["passed"] == 0
        assert out["failed"] == 0
        assert out["total"] == 0


# ============================================================
# Timeout handling
# ============================================================

class TestTimeout:
    def test_timeout_surfaces_as_timed_out_true(self, workspace: Path, monkeypatch):
        def fake_run(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"],
                                             output=b"partial\n",
                                             stderr=b"err\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        t = RunTestsTool(workspace_root=workspace, timeout_seconds=0.1)
        out = t.run()
        assert out["timed_out"] is True
        assert out["exit_code"] is None
        assert out["failed"] == 0
        assert out["failed_tests"] == []
        assert "TIMED OUT" in out["stdout_tail"]
        assert "partial" in out["stdout_tail"]
        assert "err" in out["stderr_tail"]


# ============================================================
# Redaction
# ============================================================

class TestRedaction:
    def test_secret_in_stdout_redacted(self, workspace: Path, monkeypatch):
        """A credential printed by a test must NOT survive the tool boundary."""
        secret = "sk-" + "A" * 48
        _patch_run(
            monkeypatch,
            stdout=f"1 passed in 0.01s\nleaked={secret}\n".encode("utf-8"),
        )
        out = RunTestsTool(workspace_root=workspace).run()
        assert secret not in out["stdout_tail"]
        # Even the full output contract carries the redacted form.
        assert secret not in str(out)


# ============================================================
# validate_output
# ============================================================

class TestValidateOutput:
    def _ok(self) -> dict[str, Any]:
        return {
            "command": ["python", "-m", "pytest"],
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 100,
            "passed": 1, "failed": 0, "errors": 0, "skipped": 0, "total": 1,
            "failed_tests": [],
            "stdout_truncated": False, "stderr_truncated": False,
            "stdout_tail": "1 passed", "stderr_tail": "",
            "compensation_plan": {"id": "x", "actions": [], "tool_name": "t", "description": "d"},
        }

    def test_well_formed_passes(self, workspace: Path):
        ok, warnings = RunTestsTool(workspace_root=workspace).validate_output(self._ok())
        assert ok
        assert warnings == []

    def test_non_dict_rejected(self, workspace: Path):
        ok, issues = RunTestsTool(workspace_root=workspace).validate_output("nope")
        assert not ok
        assert "dict" in issues[0]

    def test_missing_keys_rejected(self, workspace: Path):
        out = self._ok()
        del out["passed"]
        ok, issues = RunTestsTool(workspace_root=workspace).validate_output(out)
        assert not ok
        assert "missing keys" in issues[0]

    def test_negative_counts_rejected(self, workspace: Path):
        out = self._ok()
        out["passed"] = -1
        ok, issues = RunTestsTool(workspace_root=workspace).validate_output(out)
        assert not ok

    def test_total_less_than_sum_rejected(self, workspace: Path):
        out = self._ok()
        out["passed"] = 5
        out["total"] = 2
        ok, _ = RunTestsTool(workspace_root=workspace).validate_output(out)
        assert not ok

    def test_timed_out_with_exit_code_warns(self, workspace: Path):
        out = self._ok()
        out["timed_out"] = True
        out["exit_code"] = 1
        ok, warnings = RunTestsTool(workspace_root=workspace).validate_output(out)
        assert ok
        assert any("timed_out" in w for w in warnings)


# ============================================================
# Compensation plan shape
# ============================================================

class TestCompensationPlan:
    def test_always_noop(self, workspace: Path, monkeypatch):
        _patch_run(monkeypatch, stdout=b"1 passed in 0.01s\n")
        out = RunTestsTool(workspace_root=workspace).run()
        plan = out["compensation_plan"]
        assert isinstance(plan, dict)
        assert plan["actions"] == [{"kind": "noop", "description": "test run has no compensation"}]
