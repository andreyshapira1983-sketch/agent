# Run: "C:\Users\andre\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/test_python_probe_workspace_import.py -q
# Witness test for the python_probe workspace-import switch.
# On the OLD code this test FAILS (import core is impossible under -I).
# On the NEW code it PASSES, and it checks BOTH edges: switch off and switch on.
import os
import sys
from pathlib import Path

import pytest

from tools.python_probe import PythonProbeTool

WORKSPACE = Path(__file__).resolve().parents[1]


def _run_probe(monkeypatch, enabled: bool):
    if enabled:
        monkeypatch.setenv("PYTHON_PROBE_WORKSPACE_IMPORT", "1")
    else:
        monkeypatch.delenv("PYTHON_PROBE_WORKSPACE_IMPORT", raising=False)
    tool = PythonProbeTool(workspace_root=WORKSPACE)
    return tool.run(code="import core; print(core.__file__)", timeout_seconds=30)


def test_switch_off_keeps_isolation(monkeypatch):
    """Edge 1: default OFF — the probe must NOT see the workspace modules."""
    result = _run_probe(monkeypatch, enabled=False)
    combined = (result.stdout or "") + (result.stderr or "")
    assert "ModuleNotFoundError" in combined or "No module named 'core'" in combined


def test_switch_on_allows_workspace_import(monkeypatch):
    """Edge 2: switch ON — the probe CAN import core from the workspace."""
    result = _run_probe(monkeypatch, enabled=True)
    combined = (result.stdout or "") + (result.stderr or "")
    assert "ModuleNotFoundError" not in combined
    assert "core" in (result.stdout or "")
