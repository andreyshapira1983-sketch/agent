"""
tools/builtins/powershell_tool.py — PowerShell execution tool

Allows Brain to run PowerShell commands on the local machine.
Output (stdout/stderr/exit_code) is returned as structured ToolResult.

Safety rules:
  - Commands run with 30s timeout
  - Blocked keywords prevent obviously destructive commands
  - is_destructive=True so Brain must have allow_destructive=True to run
"""

from __future__ import annotations

import subprocess
import shlex
from typing import Any

from tools.base import ToolBase, ToolResult, ToolSpec

# Commands that are always blocked regardless of parameters
_BLOCKED = frozenset([
    "remove-item", "del", "rd", "rmdir",
    "format-volume", "clear-disk",
    "stop-computer", "restart-computer",
    "invoke-expression", "iex",
    "start-bitstransfer",
])


class PowerShellTool(ToolBase):
    """
    Executes a PowerShell command and returns stdout, stderr, exit_code.

    Parameters:
        command  (str): The PowerShell command to run
        timeout  (int, optional): Timeout in seconds (default 30, max 60)

    Returns dict:
        stdout    (str)
        stderr    (str)
        exit_code (int)
        truncated (bool) — True if output was cut at 4096 chars
    """

    _MAX_OUTPUT = 4096  # chars per stream

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="powershell",
            description=(
                "Выполняет команду PowerShell и возвращает stdout, stderr, exit_code. "
                "Используй для получения информации о системе, файлах, процессах, сети."
            ),
            parameters={
                "command": "str — команда PowerShell для выполнения",
                "timeout": "int (optional) — таймаут в секундах (по умолчанию 30)",
            },
            is_destructive=True,  # can modify system — require explicit approval
        )

    def execute(self, **params: Any) -> ToolResult:
        command: str = params.get("command", "").strip()
        timeout: int = min(int(params.get("timeout", 30)), 60)

        if not command:
            return self._fail("Параметр 'command' не может быть пустым")

        # Safety: block destructive keywords
        cmd_lower = command.lower()
        for blocked in _BLOCKED:
            if blocked in cmd_lower:
                return self._fail(
                    f"Команда содержит заблокированное ключевое слово: '{blocked}'. "
                    "Деструктивные операции запрещены."
                )

        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            truncated = False

            if len(stdout) > self._MAX_OUTPUT:
                stdout = stdout[:self._MAX_OUTPUT] + "\n…[truncated]"
                truncated = True
            if len(stderr) > self._MAX_OUTPUT:
                stderr = stderr[:self._MAX_OUTPUT] + "\n…[truncated]"

            return self._ok(
                output={
                    "stdout":    stdout.strip(),
                    "stderr":    stderr.strip(),
                    "exit_code": proc.returncode,
                    "truncated": truncated,
                },
                command=command,
                exit_code=proc.returncode,
            )

        except subprocess.TimeoutExpired:
            return self._fail(f"Команда превысила таймаут ({timeout}s): {command}")
        except FileNotFoundError:
            return self._fail("PowerShell не найден. Убедитесь что он установлен.")
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"Ошибка выполнения: {exc}")
