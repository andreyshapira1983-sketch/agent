"""
Инструмент для установки и обновления pip-пакетов агентом.
Запускает только python -m pip install [--upgrade] <package> — без --index-url, -e и т.д.
"""
from __future__ import annotations

import re
import subprocess  # nosec B404 — фиксированные аргументы, package из валидации
import sys

from src.tools.base import tool_schema
from src.tools.registry import register

# Допустимое имя пакета PyPI: буквы, цифры, дефис, подчёркивание, точка. Без пробелов и спецсимволов.
_PACKAGE_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")
_MAX_PACKAGE_LEN = 100
_PIP_TIMEOUT = 120


def _pip_install(package: str, upgrade: bool = False) -> str:
    """
    Установить или обновить пакет: python -m pip install [--upgrade] <package>.
    package проверяется по шаблону (только имя пакета PyPI).
    """
    pkg = (package or "").strip()
    if not pkg:
        return "Error: package name is empty."
    if len(pkg) > _MAX_PACKAGE_LEN:
        return f"Error: package name too long (max {_MAX_PACKAGE_LEN})."
    if not _PACKAGE_PATTERN.match(pkg):
        return "Error: invalid package name. Use only letters, numbers, hyphens, underscores, dots (PyPI name)."
    cmd = [sys.executable, "-m", "pip", "install", pkg]
    if upgrade:
        cmd.insert(4, "--upgrade")
    try:
        r = subprocess.run(  # nosec B603
            cmd,
            capture_output=True,
            text=True,
            timeout=_PIP_TIMEOUT,
        )
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if r.returncode != 0:
            return f"pip exit {r.returncode}\n{out}\n{err}"[:1500]
        return f"OK: {out}" if out else f"Installed/updated: {pkg}"
    except subprocess.TimeoutExpired:
        return f"Error: pip timeout ({_PIP_TIMEOUT}s)."
    except Exception as e:
        return f"Error: {e!s}"


def register_pip_tool() -> None:
    register(
        "pip_install",
        tool_schema(
            "pip_install",
            "Install or upgrade a Python package via pip (current environment). Use package=name and upgrade=True to update. Only PyPI names, no extra options.",
            {
                "package": {"type": "string", "description": "PyPI package name (e.g. requests, openai)"},
                "upgrade": {"type": "boolean", "description": "If true, run pip install --upgrade", "default": False},
            },
            required=["package"],
        ),
        lambda package, upgrade=False: _pip_install(package, upgrade=upgrade),
    )
