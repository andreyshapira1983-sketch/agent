"""Тесты для execution/command_gateway.py — CommandGateway, GatewayResult, whitelist/blocklist."""

import os
import pytest
from unittest.mock import MagicMock, patch, mock_open
from execution.command_gateway import (
    CommandGateway,
    GatewayResult,
    ALLOWED_EXECUTABLES,
    BLOCKED_EXECUTABLES,
)


# ── GatewayResult ─────────────────────────────────────────────────────────────

class TestGatewayResult:
    def test_defaults(self):
        r = GatewayResult(allowed=True)
        assert r.allowed is True
        assert r.stdout == ""
        assert r.stderr == ""
        assert r.returncode == -1
        assert r.reject_reason == ""
        assert r.duration == 0.0
        assert r.command == ""
        assert r.exe == ""

    def test_custom_values(self):
        r = GatewayResult(
            allowed=False, reject_reason="blocked",
            command="rm file", exe="rm"
        )
        assert r.allowed is False
        assert r.reject_reason == "blocked"


# ── Whitelist / Blocklist sets ────────────────────────────────────────────────

class TestExecutableLists:
    def test_allowed_contains_python(self):
        assert "python" in ALLOWED_EXECUTABLES
        assert "git" in ALLOWED_EXECUTABLES
        assert "pytest" in ALLOWED_EXECUTABLES

    def test_blocked_contains_rm(self):
        assert "rm" in BLOCKED_EXECUTABLES
        assert "sudo" in BLOCKED_EXECUTABLES

    def test_no_overlap(self):
        overlap = ALLOWED_EXECUTABLES & BLOCKED_EXECUTABLES
        assert len(overlap) == 0


# ── CommandGateway ────────────────────────────────────────────────────────────

class TestCommandGateway:
    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """Reset singleton before each test."""
        CommandGateway._instance = None
        yield
        CommandGateway._instance = None

    def _make_gw(self, **kwargs):
        defaults = dict(audit_log=os.devnull)
        defaults.update(kwargs)
        return CommandGateway(**defaults)

    # ── Singleton ──

    def test_get_instance_returns_same(self):
        gw1 = CommandGateway.get_instance(audit_log=os.devnull)
        gw2 = CommandGateway.get_instance()
        assert gw1 is gw2

    # ── Rejection: args is string ──

    def test_execute_rejects_string_args(self):
        gw = self._make_gw()
        r = gw.execute("echo hello", caller="test")
        assert r.allowed is False
        assert r.reject_reason  # has a reason

    # ── Rejection: empty args ──

    def test_execute_rejects_empty(self):
        gw = self._make_gw()
        r = gw.execute([], caller="test")
        assert r.allowed is False
        assert r.reject_reason  # has a reason

    # ── Rejection: blocked executable ──

    def test_execute_blocks_rm(self):
        gw = self._make_gw()
        r = gw.execute(["rm", "-rf", "/"], caller="test")
        assert r.allowed is False
        assert r.reject_reason

    # ── Rejection: not in whitelist ──

    def test_execute_blocks_unknown_exe(self):
        gw = self._make_gw()
        r = gw.execute(["zzz_unknown_tool"], caller="test")
        assert r.allowed is False
        assert r.reject_reason

    # ── Rejection: injection patterns in args ──

    def test_execute_blocks_pipe_injection(self):
        gw = self._make_gw()
        r = gw.execute(["python", "script.py", "|", "nc", "8.8.8.8"], caller="test")
        assert r.allowed is False
        assert r.reject_reason

    def test_execute_blocks_semicolon_injection(self):
        gw = self._make_gw()
        r = gw.execute(["git", "status", "; rm -rf /"], caller="test")
        assert r.allowed is False

    def test_execute_blocks_backtick(self):
        gw = self._make_gw()
        r = gw.execute(["python", "`whoami`"], caller="test")
        assert r.allowed is False

    def test_execute_blocks_subshell(self):
        gw = self._make_gw()
        r = gw.execute(["python", "$(whoami)"], caller="test")
        assert r.allowed is False

    def test_execute_blocks_redirect(self):
        gw = self._make_gw()
        r = gw.execute(["echo", "data", ">", "/etc/passwd"], caller="test")
        assert r.allowed is False

    def test_execute_blocks_traversal(self):
        gw = self._make_gw()
        r = gw.execute(["cat", "../../etc/passwd"], caller="test")
        assert r.allowed is False

    # ── Rejection: python -c with network ──

    def test_execute_blocks_python_c_network(self):
        gw = self._make_gw()
        r = gw.execute(["python", "-c", "import socket"], caller="test")
        assert r.allowed is False
        assert r.reject_reason

    def test_execute_blocks_python_c_requests(self):
        gw = self._make_gw()
        r = gw.execute(["python", "-c", "import requests"], caller="test")
        assert r.allowed is False

    # ── Successful execution (mocked subprocess) ──

    def test_execute_success(self):
        gw = self._make_gw()
        fake_proc = MagicMock()
        fake_proc.stdout = "output"
        fake_proc.stderr = ""
        fake_proc.returncode = 0
        with patch("execution.command_gateway.subprocess.run", return_value=fake_proc):
            r = gw.execute(["python", "--version"], caller="test")
            assert r.allowed is True
            assert r.stdout == "output"
            assert r.returncode == 0

    # ── Timeout ──

    def test_execute_timeout(self):
        gw = self._make_gw()
        import subprocess as sp
        with patch("execution.command_gateway.subprocess.run",
                   side_effect=sp.TimeoutExpired(cmd="python", timeout=5)):
            r = gw.execute(["python", "long.py"], timeout=5, caller="test")
            assert r.allowed is True
            assert r.returncode == -1
            assert r.reject_reason  # timeout message

    # ── OSError ──

    def test_execute_os_error(self):
        gw = self._make_gw()
        with patch("execution.command_gateway.subprocess.run",
                   side_effect=OSError("No such file")):
            r = gw.execute(["python", "missing.py"], caller="test")
            assert r.allowed is True
            assert r.returncode == -1

    # ── .exe stripping ──

    def test_execute_strips_exe_extension(self):
        gw = self._make_gw()
        fake_proc = MagicMock(stdout="ok", stderr="", returncode=0)
        with patch("execution.command_gateway.subprocess.run", return_value=fake_proc):
            r = gw.execute(["python.exe", "--version"], caller="test")
            assert r.allowed is True
            assert r.exe == "python"

    # ── execute_str ──

    def test_execute_str_parses_command(self):
        gw = self._make_gw()
        fake_proc = MagicMock(stdout="ok", stderr="", returncode=0)
        with patch("execution.command_gateway.subprocess.run", return_value=fake_proc):
            r = gw.execute_str("python --version", caller="test")
            assert r.allowed is True

    def test_execute_str_empty(self):
        gw = self._make_gw()
        r = gw.execute_str("", caller="test")
        assert r.allowed is False
        assert r.reject_reason

    def test_execute_str_whitespace(self):
        gw = self._make_gw()
        r = gw.execute_str("   ", caller="test")
        assert r.allowed is False

    def test_execute_str_invalid_syntax(self):
        gw = self._make_gw()
        r = gw.execute_str("echo 'unterminated", caller="test")
        assert r.allowed is False
        assert r.reject_reason

    # ── Stats ──

    def test_stats_increments(self):
        gw = self._make_gw()
        gw.execute([], caller="test")           # blocked (empty)
        gw.execute(["rm", "file"], caller="test")  # blocked (blocked exe)
        assert gw.stats["total_calls"] == 2
        assert gw.stats["blocked"] >= 1

    # ── Docker wrap ──

    def test_wrap_docker_basic(self):
        gw = self._make_gw(docker_image="myimage:latest", working_dir="/work")
        args = gw._wrap_docker(["python", "script.py"], allow_network=False)
        assert "docker" in args[0]
        assert "--network" in args
        assert "none" in args
        assert "myimage:latest" in args
        assert "python" in args

    def test_wrap_docker_with_network(self):
        gw = self._make_gw(docker_image="myimage:latest")
        args = gw._wrap_docker(["python", "script.py"], allow_network=True)
        assert "--network" not in args

    # ── Audit ──

    def test_audit_writes_to_file(self, tmp_path):
        audit_file = tmp_path / "audit.jsonl"
        gw = CommandGateway(audit_log=str(audit_file))
        r = GatewayResult(allowed=True, command="test cmd", exe="test")
        gw._audit(r, caller="test_caller")
        content = audit_file.read_text()
        assert "test_caller" in content
        assert "test" in content

    def test_audit_scrubber(self, tmp_path):
        audit_file = tmp_path / "audit.jsonl"
        gw = CommandGateway(audit_log=str(audit_file))
        gw._scrubber = lambda s: s.replace("secret", "***")
        r = GatewayResult(allowed=True, command="cmd with secret", exe="cmd")
        gw._audit(r, caller="test")
        content = audit_file.read_text()
        assert "secret" not in content
        assert "***" in content

    def test_audit_with_monitoring(self, tmp_path):
        mon = MagicMock()
        audit_file = tmp_path / "audit.jsonl"
        gw = CommandGateway(monitoring=mon, audit_log=str(audit_file))
        r = GatewayResult(allowed=False, reject_reason="test", command="bad", exe="bad")
        gw._audit(r, caller="test")
        mon.log.assert_called_once()

    # ── _reject ──

    def test_reject_returns_gateway_result(self):
        gw = self._make_gw()
        r = gw._reject("cmd", "reason_test", exe="exe", caller="caller")
        assert r.allowed is False
        assert r.reject_reason == "reason_test"
        assert r.exe == "exe"
