"""Architecture-only Windows service shell for daemon plan item 1.4.

The project has selected ``pywin32`` as the single Windows service mechanism.
This module fixes the service contract without yet installing, removing,
starting, stopping, or configuring recovery for a real service. Those actions
belong to later roadmap items and must not be added here implicitly.

The module is safe to import on non-Windows systems: pywin32 is never imported
at module import time. Runtime checks fail with clear messages instead.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

SERVICE_NAME = "AgentDaemon"
DISPLAY_NAME = "Agent Daemon"
DEFAULT_DAEMON_MODULE = "app.daemon"
DEFAULT_STOP_TIMEOUT_SECONDS = 30.0
DEFAULT_SERVICE_ACCOUNT = r"NT AUTHORITY\LocalService"
DEFAULT_WORKSPACE = Path(__file__).resolve().parents[1]
RESTART_POLICY_DESCRIPTION = (
    "Restart only after an unexpected crash, with bounded attempts and "
    "increasing backoff; Windows recovery configuration is deferred to "
    "roadmap item 0.1.4."
)


def _is_windows() -> bool:
    """Return whether the current host is Windows.

    Kept as a single indirection so tests can simulate the host OS without
    mutating the global ``os.name`` (which would make ``pathlib.Path`` build
    ``WindowsPath`` on POSIX and crash).
    """

    return os.name == "nt"


class WindowsServiceShellError(RuntimeError):
    """Raised when the architecture shell cannot be used in this environment."""


class WindowsServiceConfigError(ValueError):
    """Raised when the service contract is internally inconsistent."""


@dataclass(frozen=True)
class WindowsServiceContract:
    """Resolved, machine-local contract for the future pywin32 service.

    Paths are computed at runtime. No absolute path from a specific machine is
    stored in the repository.
    """

    workspace: Path = DEFAULT_WORKSPACE
    venv_dir: Path | None = None
    service_name: str = SERVICE_NAME
    display_name: str = DISPLAY_NAME
    daemon_module: str = DEFAULT_DAEMON_MODULE
    service_account: str = DEFAULT_SERVICE_ACCOUNT
    stop_timeout_seconds: float = DEFAULT_STOP_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls) -> "WindowsServiceContract":
        """Build the contract from optional non-secret environment overrides."""

        workspace_raw = os.getenv("AGENT_SERVICE_WORKSPACE")
        workspace = (
            Path(workspace_raw).expanduser()
            if workspace_raw
            else DEFAULT_WORKSPACE
        )
        venv_raw = os.getenv("AGENT_SERVICE_VENV")
        venv_dir = Path(venv_raw).expanduser() if venv_raw else None
        account = os.getenv("AGENT_SERVICE_ACCOUNT", DEFAULT_SERVICE_ACCOUNT)
        timeout_raw = os.getenv("AGENT_SERVICE_STOP_TIMEOUT_SECONDS")
        if timeout_raw is None:
            timeout = DEFAULT_STOP_TIMEOUT_SECONDS
        else:
            try:
                timeout = float(timeout_raw)
            except ValueError as exc:
                raise WindowsServiceConfigError(
                    "AGENT_SERVICE_STOP_TIMEOUT_SECONDS must be a number"
                ) from exc
        contract = cls(
            workspace=workspace,
            venv_dir=venv_dir,
            service_account=account,
            stop_timeout_seconds=timeout,
        )
        contract.validate_contract()
        return contract

    @property
    def working_directory(self) -> Path:
        return self.workspace

    @property
    def resolved_venv_dir(self) -> Path:
        return self.venv_dir or (self.workspace / ".venv")

    @property
    def python_executable(self) -> Path:
        return self.resolved_venv_dir / "Scripts" / "python.exe"

    @property
    def data_directory(self) -> Path:
        return self.workspace / "data"

    @property
    def log_directory(self) -> Path:
        return self.workspace / "logs"

    @property
    def command(self) -> tuple[str, ...]:
        return (str(self.python_executable), "-m", self.daemon_module)

    def validate_contract(self) -> None:
        """Validate values that do not require Windows or pywin32."""

        if not self.service_name.strip():
            raise WindowsServiceConfigError("service_name must not be empty")
        if not self.daemon_module.strip():
            raise WindowsServiceConfigError("daemon_module must not be empty")
        if self.stop_timeout_seconds <= 0:
            raise WindowsServiceConfigError(
                "stop_timeout_seconds must be greater than zero"
            )
        if not self.service_account.strip():
            raise WindowsServiceConfigError("service_account must not be empty")

    def validate_runtime(self) -> None:
        """Validate the future service host environment without changing it."""

        self.validate_contract()
        if not _is_windows():
            raise WindowsServiceShellError(
                "Windows service runtime validation is available only on Windows"
            )
        missing = [
            name
            for name in ("win32service", "win32serviceutil", "servicemanager")
            if importlib.util.find_spec(name) is None
        ]
        if missing:
            joined = ", ".join(missing)
            raise WindowsServiceShellError(
                f"pywin32 runtime modules are not installed: {joined}; "
                "dependency installation is a separate roadmap task"
            )
        if not self.workspace.is_dir():
            raise WindowsServiceShellError(
                f"service workspace does not exist: {self.workspace}"
            )
        if not self.python_executable.is_file():
            raise WindowsServiceShellError(
                f"virtual-environment interpreter does not exist: "
                f"{self.python_executable}"
            )

    def as_dict(self) -> dict[str, object]:
        """Return the observable architecture contract for operators."""

        self.validate_contract()
        return {
            "architecture_status": "shell_only",
            "mechanism": "pywin32",
            "service_name": self.service_name,
            "display_name": self.display_name,
            "executable": str(self.python_executable),
            "command": list(self.command),
            "working_directory": str(self.working_directory),
            "service_account": self.service_account,
            "data_directory": str(self.data_directory),
            "log_directory": str(self.log_directory),
            "stop_timeout_seconds": self.stop_timeout_seconds,
            "restart_policy": RESTART_POLICY_DESCRIPTION,
            "install_implemented": False,
            "uninstall_implemented": False,
            "start_stop_implemented": False,
            "recovery_policy_implemented": False,
        }


def _usage() -> str:
    return (
        "Usage: python -m app.windows_service "
        "[--show-contract | --check-runtime]\n"
        "This is an architecture shell only; install/uninstall/start/stop are "
        "intentionally not implemented."
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Expose the shell contract without performing service mutations."""

    args = list(sys.argv[1:] if argv is None else argv)
    try:
        contract = WindowsServiceContract.from_environment()
        if not args or args == ["--show-contract"]:
            print(json.dumps(contract.as_dict(), indent=2, ensure_ascii=False))
            return 0
        if args == ["--check-runtime"]:
            contract.validate_runtime()
            print("Windows service runtime prerequisites are available.")
            return 0
        print(_usage(), file=sys.stderr)
        return 2
    except (WindowsServiceConfigError, WindowsServiceShellError) as exc:
        print(f"windows service shell error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
