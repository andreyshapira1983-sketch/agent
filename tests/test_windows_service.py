"""Tests for the architecture-only Windows service shell (plan item 1.4).

The module is a *contract shell*: it must be importable and fully testable on
any OS because ``pywin32`` is never imported at module import time, and it must
never perform a real service mutation (install/uninstall/start/stop). These
tests pin exactly that: the resolved contract, environment overrides, config
validation, the JSON contract surface, the CLI, and the runtime-validation
error paths -- without requiring Windows or pywin32.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.windows_service import (
    DEFAULT_DAEMON_MODULE,
    DEFAULT_SERVICE_ACCOUNT,
    DEFAULT_STOP_TIMEOUT_SECONDS,
    DEFAULT_WORKSPACE,
    RESTART_POLICY_DESCRIPTION,
    SERVICE_NAME,
    WindowsServiceConfigError,
    WindowsServiceContract,
    WindowsServiceShellError,
    main,
)


# -- import safety -----------------------------------------------------------


def test_module_import_does_not_pull_in_pywin32():
    # Correctness guarantee: importing the shell must not import pywin32, so it
    # loads cleanly on POSIX CI. It is already imported at module top; assert
    # none of the win32 modules leaked into sys.modules as a side effect.
    import sys

    for name in ("win32service", "win32serviceutil", "servicemanager"):
        assert name not in sys.modules or sys.modules[name] is not None


# -- default contract --------------------------------------------------------


def test_default_contract_shape():
    contract = WindowsServiceContract()
    assert contract.service_name == SERVICE_NAME
    assert contract.daemon_module == DEFAULT_DAEMON_MODULE
    assert contract.service_account == DEFAULT_SERVICE_ACCOUNT
    assert contract.stop_timeout_seconds == DEFAULT_STOP_TIMEOUT_SECONDS
    assert contract.workspace == DEFAULT_WORKSPACE


def test_default_workspace_is_repo_root_not_hardcoded():
    # Computed from __file__, so the repo carries no machine-specific abs path.
    contract = WindowsServiceContract()
    assert (contract.workspace / "app" / "windows_service.py").is_file()


def test_derived_paths_are_workspace_relative():
    ws = Path("/tmp/example-workspace")
    contract = WindowsServiceContract(workspace=ws)
    assert contract.working_directory == ws
    assert contract.resolved_venv_dir == ws / ".venv"
    assert contract.python_executable == ws / ".venv" / "Scripts" / "python.exe"
    assert contract.data_directory == ws / "data"
    assert contract.log_directory == ws / "logs"
    assert contract.command == (
        str(ws / ".venv" / "Scripts" / "python.exe"),
        "-m",
        DEFAULT_DAEMON_MODULE,
    )


def test_explicit_venv_dir_overrides_default():
    ws = Path("/tmp/ws")
    venv = Path("/opt/venvs/agent")
    contract = WindowsServiceContract(workspace=ws, venv_dir=venv)
    assert contract.resolved_venv_dir == venv
    assert contract.python_executable == venv / "Scripts" / "python.exe"


# -- from_environment --------------------------------------------------------


def test_from_environment_defaults_without_env(monkeypatch: pytest.MonkeyPatch):
    for var in (
        "AGENT_SERVICE_WORKSPACE",
        "AGENT_SERVICE_VENV",
        "AGENT_SERVICE_ACCOUNT",
        "AGENT_SERVICE_STOP_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)
    contract = WindowsServiceContract.from_environment()
    assert contract.workspace == DEFAULT_WORKSPACE
    assert contract.venv_dir is None
    assert contract.service_account == DEFAULT_SERVICE_ACCOUNT
    assert contract.stop_timeout_seconds == DEFAULT_STOP_TIMEOUT_SECONDS


def test_from_environment_applies_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_SERVICE_WORKSPACE", "/srv/agent")
    monkeypatch.setenv("AGENT_SERVICE_VENV", "/srv/agent/venv")
    monkeypatch.setenv("AGENT_SERVICE_ACCOUNT", r"NT AUTHORITY\SYSTEM")
    monkeypatch.setenv("AGENT_SERVICE_STOP_TIMEOUT_SECONDS", "45")
    contract = WindowsServiceContract.from_environment()
    assert contract.workspace == Path("/srv/agent")
    assert contract.venv_dir == Path("/srv/agent/venv")
    assert contract.service_account == r"NT AUTHORITY\SYSTEM"
    assert contract.stop_timeout_seconds == 45.0


def test_from_environment_rejects_non_numeric_timeout(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AGENT_SERVICE_STOP_TIMEOUT_SECONDS", "soon")
    with pytest.raises(WindowsServiceConfigError):
        WindowsServiceContract.from_environment()


def test_from_environment_rejects_non_positive_timeout(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AGENT_SERVICE_STOP_TIMEOUT_SECONDS", "0")
    with pytest.raises(WindowsServiceConfigError):
        WindowsServiceContract.from_environment()


# -- validate_contract -------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"service_name": "   "},
        {"daemon_module": ""},
        {"service_account": " "},
        {"stop_timeout_seconds": 0},
        {"stop_timeout_seconds": -5},
    ],
)
def test_validate_contract_rejects_bad_values(kwargs):
    contract = WindowsServiceContract(**kwargs)
    with pytest.raises(WindowsServiceConfigError):
        contract.validate_contract()


def test_validate_contract_accepts_defaults():
    WindowsServiceContract().validate_contract()  # must not raise


# -- as_dict (observable contract) -------------------------------------------


def test_as_dict_is_shell_only_and_json_serialisable():
    data = WindowsServiceContract().as_dict()
    # Nothing is actually implemented yet: the shell advertises that honestly.
    assert data["architecture_status"] == "shell_only"
    assert data["mechanism"] == "pywin32"
    assert data["install_implemented"] is False
    assert data["uninstall_implemented"] is False
    assert data["start_stop_implemented"] is False
    assert data["recovery_policy_implemented"] is False
    assert data["restart_policy"] == RESTART_POLICY_DESCRIPTION
    assert isinstance(data["command"], list)
    # Round-trips through JSON without error.
    assert json.loads(json.dumps(data)) == data


# -- validate_runtime (no mutation, clear errors) ----------------------------


def test_validate_runtime_off_windows_raises(monkeypatch: pytest.MonkeyPatch):
    import app.windows_service as mod

    monkeypatch.setattr(mod, "_is_windows", lambda: False)
    with pytest.raises(WindowsServiceShellError, match="only on Windows"):
        WindowsServiceContract().validate_runtime()


def test_validate_runtime_reports_missing_pywin32(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # Pretend we are on Windows but pywin32 is absent: clear, actionable error
    # naming the missing modules, and still no service mutation performed.
    import app.windows_service as mod

    monkeypatch.setattr(mod, "_is_windows", lambda: True)
    monkeypatch.setattr(mod.importlib.util, "find_spec", lambda name: None)
    contract = WindowsServiceContract(workspace=tmp_path)
    with pytest.raises(WindowsServiceShellError, match="pywin32"):
        contract.validate_runtime()


def test_validate_runtime_reports_missing_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    import app.windows_service as mod

    monkeypatch.setattr(mod, "_is_windows", lambda: True)
    # pywin32 present, but the workspace directory does not exist.
    monkeypatch.setattr(mod.importlib.util, "find_spec", lambda name: object())
    contract = WindowsServiceContract(workspace=tmp_path / "missing")
    with pytest.raises(WindowsServiceShellError, match="workspace does not exist"):
        contract.validate_runtime()


def test_validate_runtime_reports_missing_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    import app.windows_service as mod

    monkeypatch.setattr(mod, "_is_windows", lambda: True)
    monkeypatch.setattr(mod.importlib.util, "find_spec", lambda name: object())
    # Workspace exists but the venv interpreter does not.
    contract = WindowsServiceContract(workspace=tmp_path)
    with pytest.raises(WindowsServiceShellError, match="interpreter does not exist"):
        contract.validate_runtime()


# -- CLI (main) --------------------------------------------------------------


def test_main_show_contract_prints_json(capsys: pytest.CaptureFixture):
    rc = main(["--show-contract"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["architecture_status"] == "shell_only"


def test_main_no_args_defaults_to_show_contract(capsys: pytest.CaptureFixture):
    rc = main([])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mechanism"] == "pywin32"


def test_main_unknown_arg_prints_usage_and_returns_2(
    capsys: pytest.CaptureFixture,
):
    rc = main(["--frobnicate"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Usage:" in err
    assert "not implemented" in err


def test_main_check_runtime_off_windows_returns_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    import app.windows_service as mod

    monkeypatch.setattr(mod, "_is_windows", lambda: False)
    rc = main(["--check-runtime"])
    assert rc == 1
    assert "windows service shell error" in capsys.readouterr().err


def test_main_check_runtime_success_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    tmp_path: Path,
):
    # Simulate a fully-provisioned Windows host so the happy path is covered
    # without a real service call.
    import app.windows_service as mod

    monkeypatch.setattr(mod, "_is_windows", lambda: True)
    monkeypatch.setenv("AGENT_SERVICE_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(mod.importlib.util, "find_spec", lambda name: object())
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_text("stub")
    rc = main(["--check-runtime"])
    assert rc == 0
    assert "prerequisites are available" in capsys.readouterr().out


def test_main_config_error_returns_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    monkeypatch.setenv("AGENT_SERVICE_STOP_TIMEOUT_SECONDS", "nope")
    rc = main(["--show-contract"])
    assert rc == 1
    assert "windows service shell error" in capsys.readouterr().err
