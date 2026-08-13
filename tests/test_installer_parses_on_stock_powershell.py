"""Установщик вахты обязан парситься на СТОКОВОМ Windows PowerShell 5.1.

ЖИВОЙ СЛУЧАЙ 2026-08-13: оператор запустил `powershell -File
scripts\\install_daemon.ps1` и получил ParserError на `?.Source` —
null-условный доступ существует только в pwsh 7. Команда `powershell` на
любой Windows — это движок 5.1, и именно им пользуются по инструкции.
Парсинг без исполнения: регистрация задачи — действие оператора, не теста.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "install_daemon.ps1"

#: Настоящий парсер-API, не `[scriptblock]::Create` — тот в 5.1 глотал `?.`
#: и первая версия оракула не ловила живой баг (ломка не покраснела).
_PARSE = (
    "$errs = $null; "
    "[System.Management.Automation.Language.Parser]::ParseFile("
    "'{path}', [ref]$null, [ref]$errs) | Out-Null; "
    "exit $errs.Count"
)


@pytest.mark.parametrize("engine", ["powershell", "pwsh"])
def test_the_installer_parses(engine: str) -> None:
    if shutil.which(engine) is None:
        pytest.skip(f"{engine} отсутствует в этом окружении")
    result = subprocess.run(  # noqa: S603 — argv фиксирован, вход — наш файл
        [engine, "-NoProfile", "-NonInteractive", "-Command",
         _PARSE.format(path=str(_SCRIPT).replace("'", "''"))],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, (
        f"{engine} не парсит установщик: {result.stderr[:300]}"
    )
