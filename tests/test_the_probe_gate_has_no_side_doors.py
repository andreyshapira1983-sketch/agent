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


def test_a_lawful_measurement_is_not_refused() -> None:
    for code in ("import importlib.util\nprint(importlib.util.find_spec('json') is not None)",
                 "import importlib.metadata as m\nprint(m.version('pytest'))",
                 "import json, math\nprint(json.dumps({'pi': math.pi}))"):
        assert _forbidden_reason(code) is None, code


def _run(code: str) -> dict:
    return PythonProbeTool(workspace_root=None).run(code=code, timeout_seconds=30)


def test_a_process_started_past_the_text_gate_is_stopped_at_run_time() -> None:
    code = ("import importlib\n"
            "m = getattr(importlib, 'import_' + 'module')('subprocess')\n"
            "m.run(['echo', 'escaped'])\n")
    assert _forbidden_reason(code) is None, "this one slips the text gate on purpose"
    out = _run(code)
    assert out["exit_code"] != 0
    assert "refused at run time: subprocess.Popen" in out["stderr"]
    assert "escaped" not in out["stdout"]


def test_writing_outside_the_experiment_folder_is_stopped(tmp_path: Path) -> None:
    target = Path(__file__).resolve().parent / "_probe_escape_marker.txt"
    target.unlink(missing_ok=True)
    try:
        out = _run(f"from pathlib import Path\nPath({str(target)!r}).write_text('x')\n")
        assert "refused at run time: writing outside the experiment folder" in out["stderr"]
        assert not target.exists()
    finally:
        target.unlink(missing_ok=True)


def test_writing_inside_the_experiment_folder_still_works() -> None:
    out = _run("from pathlib import Path\nPath('note.txt').write_text('ok')\n"
               "print(Path('note.txt').read_text())\n")
    assert out["exit_code"] == 0 and out["stdout"].strip() == "ok", out


def test_a_failing_line_keeps_its_own_number() -> None:
    out = _run("x = 1\ny = 2\nraise ValueError('line three')\n")
    assert 'line 3' in out["stderr"] and "line three" in out["stderr"]
