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
    WindowsServiceConfigError,
    WindowsServiceContract,
    WindowsServiceShellError,
    main,
)

# -- from_environment --------------------------------------------------------


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
