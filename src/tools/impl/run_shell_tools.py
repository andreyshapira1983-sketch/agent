"""
Инструменты для запуска Python и PowerShell агентом.
Без shell=True, фиксированные исполняемые файлы, таймаут и лимит длины.
"""
from __future__ import annotations

import sys
import subprocess  # nosec B404 — команда фиксирована (python/powershell), ввод из аргумента

from src.tools.base import tool_schema
from src.tools.registry import register

_PYTHON_TIMEOUT = 60.0
_POWERSHELL_TIMEOUT = 30.0
_MAX_PYTHON_CODE_LEN = 20_000
_MAX_POWERSHELL_SCRIPT_LEN = 10_000


def _run_python(code: str) -> str:
    """Выполнить код Python: sys.executable -c <code>. Без shell."""
    c = (code or "").strip()
    if not c:
        return "Error: empty code."
    if len(c) > _MAX_PYTHON_CODE_LEN:
        return f"Error: code too long (max {_MAX_PYTHON_CODE_LEN} chars)."
    try:
        r = subprocess.run(  # nosec B603
            [sys.executable, "-c", c],
            capture_output=True,
            text=True,
            timeout=_PYTHON_TIMEOUT,
        )
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if r.returncode != 0:
            return f"Exit {r.returncode}\n{out}\n{err}"[:2000]
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: timeout ({_PYTHON_TIMEOUT}s)."
    except Exception as e:
        return f"Error: {e!s}"


def _run_powershell(script: str) -> str:
    """Выполнить скрипт PowerShell: powershell -NoProfile -NonInteractive -Command <script>. Без shell."""
    s = (script or "").strip()
    if not s:
        return "Error: empty script."
    if len(s) > _MAX_POWERSHELL_SCRIPT_LEN:
        return f"Error: script too long (max {_MAX_POWERSHELL_SCRIPT_LEN} chars)."
    try:
        r = subprocess.run(  # nosec B603
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", s],
            capture_output=True,
            text=True,
            timeout=_POWERSHELL_TIMEOUT,
        )
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if r.returncode != 0:
            return f"Exit {r.returncode}\n{out}\n{err}"[:2000]
        return out or "(no output)"
    except FileNotFoundError:
        return "Error: powershell not found (Windows?)."
    except subprocess.TimeoutExpired:
        return f"Error: timeout ({_POWERSHELL_TIMEOUT}s)."
    except Exception as e:
        return f"Error: {e!s}"


def register_run_shell_tools() -> None:
    register(
        "run_python",
        tool_schema(
            "run_python",
            "Run Python code in current interpreter (subprocess, timeout 60s). Use for scripts, one-liners, data processing.",
            {"code": {"type": "string", "description": "Python code to execute (e.g. print(1+1), import os; print(os.getcwd())"}},
            required=["code"],
        ),
        _run_python,
    )
    register(
        "run_powershell",
        tool_schema(
            "run_powershell",
            "Run PowerShell script on Windows (subprocess, timeout 30s). Use for dir, Get-*, environment, file ops.",
            {"script": {"type": "string", "description": "PowerShell script (e.g. Get-Location, Get-ChildItem, $env:USERNAME)"}},
            required=["script"],
        ),
        _run_powershell,
    )
