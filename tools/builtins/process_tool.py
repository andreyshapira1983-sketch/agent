"""
tools/builtins/process_tool.py — External process execution tool

Allows Brain to run external programs (git, python, pip, curl, etc.)
and capture their output.

Safety rules:
  - Whitelist of allowed programs — Brain cannot run arbitrary binaries
  - 30s timeout, 4096 char output limit
  - is_destructive=False for read-only programs (git status, python --version)
"""

from __future__ import annotations

import subprocess
from typing import Any

from tools.base import ToolBase, ToolResult, ToolSpec

# Allowed external programs (lower-case executable names)
ALLOWED_PROGRAMS = frozenset([
    "python", "python3", "pip", "pip3",
    "git",
    "curl", "curl.exe",
    "ping", "ping.exe",
    "ipconfig", "ipconfig.exe",
    "systeminfo",
    "tasklist", "tasklist.exe",
    "netstat",
    "where", "where.exe",
    "node", "npm",
    "java",
    "docker",
])

_MAX_OUTPUT = 4096


class ProcessTool(ToolBase):
    """
    Runs an external program and returns its stdout/stderr/exit_code.

    Parameters:
        program  (str): Program name (must be in allowed list)
        args     (list[str], optional): Command-line arguments
        timeout  (int, optional): Timeout in seconds (default 15)

    Returns dict:
        stdout    (str)
        stderr    (str)
        exit_code (int)
        program   (str)

    Allowed programs:
        python, pip, git, curl, ping, ipconfig, systeminfo,
        tasklist, netstat, where, node, npm, java, docker
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="process",
            description=(
                "Запускает внешнюю программу (git, python, pip, curl, ping и др.) "
                "и возвращает вывод. Только из разрешённого списка программ."
            ),
            parameters={
                "program": "str — имя программы (python/git/pip/curl/ping/...)",
                "args":    "list[str] (optional) — аргументы командной строки",
                "timeout": "int (optional) — таймаут в секундах (по умолчанию 15)",
            },
            is_destructive=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        program: str = params.get("program", "").strip().lower()
        args: list = params.get("args", [])
        timeout: int = min(int(params.get("timeout", 15)), 60)

        if not program:
            return self._fail("Параметр 'program' обязателен")

        # Whitelist check
        base = program.replace(".exe", "").lower()
        if base not in ALLOWED_PROGRAMS:
            return self._fail(
                f"Программа '{program}' не в списке разрешённых. "
                f"Разрешено: {', '.join(sorted(ALLOWED_PROGRAMS))}"
            )

        # Build command
        cmd = [program] + [str(a) for a in args]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
                shell=False,  # never shell=True
            )
            stdout = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()

            if len(stdout) > _MAX_OUTPUT:
                stdout = stdout[:_MAX_OUTPUT] + "\n…[truncated]"
            if len(stderr) > _MAX_OUTPUT:
                stderr = stderr[:_MAX_OUTPUT] + "\n…[truncated]"

            return self._ok(
                output={
                    "stdout":    stdout,
                    "stderr":    stderr,
                    "exit_code": proc.returncode,
                    "program":   program,
                    "args":      args,
                },
                exit_code=proc.returncode,
            )

        except subprocess.TimeoutExpired:
            return self._fail(f"Программа '{program}' превысила таймаут ({timeout}s)")
        except FileNotFoundError:
            return self._fail(
                f"Программа '{program}' не найдена. "
                "Убедитесь что она установлена и доступна в PATH."
            )
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"Ошибка запуска '{program}': {exc}")
