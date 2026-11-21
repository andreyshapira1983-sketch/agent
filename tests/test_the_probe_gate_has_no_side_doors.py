"""Гейт лаборатории без боковых дверей (24.09, слово оператора).

Опытом 24.09: `import subprocess` отказывался, но `importlib.import_module(
"subprocess")`, `from importlib import import_module`, `builtins.__import__` и
`Path(...).write_text(...)` вне папки опыта проходили. Карточки прошлых ошибок
успели выучить урок «используй importlib.import_module». Литература (smolagents
CVE-2025-5120 и CVE-2025-9959, документация Python об audit hooks): Python нельзя
запереть изнутри Python — это слои поверх, а стена — отдельный пользователь.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.python_probe import PythonProbeTool, _forbidden_reason


@pytest.mark.parametrize("code", [
    "import importlib\nimportlib.import_module('subprocess')",
    "from importlib import import_module\nimport_module('socket')",
    "import builtins\nbuiltins.__import__('subprocess')",
    "import importlib\ngetattr(importlib, 'import_module')('os')",
    "f = vars(__builtins__)['__import__']",
    "exec('import subprocess')",
    "eval('1+1')",
])
def test_every_known_side_door_is_refused_before_running(code: str) -> None:
    assert _forbidden_reason(code), code


def _run(code: str) -> dict:
    return PythonProbeTool(workspace_root=None).run(code=code, timeout_seconds=30)


def test_the_experiment_runs_as_nobody_when_the_host_allows(monkeypatch, tmp_path: Path) -> None:
    from tools import convert_file, python_probe

    monkeypatch.setattr(convert_file, "sandbox_available", lambda: None)
    monkeypatch.setattr(python_probe.os, "chown", lambda *a, **k: None, raising=False)
    argv, sandbox = python_probe._as_nobody(["python", "-c", "1"], str(tmp_path))
    assert argv[:5] == ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups",
                        "--no-new-privs"] and sandbox == "nobody"


def test_without_the_wall_a_root_process_refuses_to_run(monkeypatch, tmp_path: Path) -> None:
    from tools import convert_file, python_probe

    monkeypatch.setattr(convert_file, "sandbox_available", lambda: "setpriv is not installed")
    monkeypatch.setattr(python_probe.sys, "platform", "linux")
    monkeypatch.setattr(python_probe.os, "geteuid", lambda: 0, raising=False)
    with pytest.raises(ValueError, match="does not run as root without the sandbox"):
        python_probe._as_nobody(["python"], str(tmp_path))


needs_sandbox = pytest.mark.skipif(
    __import__("tools.convert_file", fromlist=["x"]).sandbox_available() is not None,
    reason="нужна песочница: Linux, root, setpriv")


@needs_sandbox
def test_on_the_server_the_experiment_is_nobody() -> None:
    out = _run("import os\nprint(os.getuid())\n")
    assert out["sandbox"] == "nobody" and out["stdout"].strip() == "65534", out


@needs_sandbox
def test_on_the_server_a_600_file_is_closed_and_a_644_file_is_open() -> None:
    import os
    import shutil
    import tempfile

    folder = Path(tempfile.mkdtemp())
    try:
        folder.chmod(0o755)
        (folder / "key.env").write_text("KEY=do-not-leak", encoding="utf-8")
        (folder / "key.env").chmod(0o600)
        (folder / "public.txt").write_text("hello", encoding="utf-8")
        (folder / "public.txt").chmod(0o644)
        closed = _run(f"print(open({str(folder / 'key.env')!r}).read())")
        opened = _run(f"print(open({str(folder / 'public.txt')!r}).read())")
        assert "PermissionError" in closed["stderr"] and "do-not-leak" not in closed["stdout"]
        assert opened["stdout"].strip() == "hello", "the control file must be readable"
        wrote = _run(f"from pathlib import Path\nPath({str(folder / 'x.txt')!r}).write_text('x')")
        assert "PermissionError" in wrote["stderr"] and not (folder / "x.txt").exists()
        assert os.stat(folder).st_uid == 0
    finally:
        shutil.rmtree(folder, ignore_errors=True)
