# Run: "C:\Users\andre\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/test_python_probe_workspace_import.py -q
# Witness test for the python_probe workspace-import switch.
# On the OLD code this test FAILS (import core is impossible under -I).
# On the NEW code it PASSES, and it checks BOTH edges: switch off and switch on.
#
# Свидетеля написал агент (открытая запись probe-cwd-isolation-2026-09-20).
# 2026-09-21 его обвязка была сломана: `tool.run` возвращает dict, а тест
# читал `result.stdout` как атрибут — поэтому падали ОБА края, включая тот,
# что на старом коде обязан проходить. Свидетель, падающий всегда, не
# свидетельствует ни о чём. Обвязка читает словарь; замысел не тронут.
from pathlib import Path

from tools.python_probe import PythonProbeTool

WORKSPACE = Path(__file__).resolve().parents[1]
_SWITCH = "PYTHON_PROBE_WORKSPACE_IMPORT"


def _run_probe(monkeypatch, enabled: bool, code: str = "import core; print(core.__file__)"):
    if enabled:
        monkeypatch.setenv(_SWITCH, "1")
    else:
        monkeypatch.delenv(_SWITCH, raising=False)
    tool = PythonProbeTool(workspace_root=WORKSPACE)
    return tool.run(code=code, timeout_seconds=30)


def _combined(result: dict) -> str:
    return (result.get("stdout") or "") + (result.get("stderr") or "")


def test_switch_off_keeps_isolation(monkeypatch):
    """Edge 1: default OFF — the probe must NOT see the workspace modules."""
    result = _run_probe(monkeypatch, enabled=False)
    assert "No module named 'core'" in _combined(result)


def test_switch_on_allows_workspace_import(monkeypatch):
    """Edge 2: switch ON — the probe CAN import core from the workspace."""
    result = _run_probe(monkeypatch, enabled=True)
    assert "ModuleNotFoundError" not in _combined(result)
    printed = Path((result.get("stdout") or "").strip())
    assert printed.resolve().parent == (WORKSPACE / "core").resolve()


def test_isolation_holds_even_with_the_switch_on(monkeypatch):
    """`-I` не снят: переменная PYTHONPATH снаружи в лабораторию не проходит."""
    monkeypatch.setenv("PYTHONPATH", str(WORKSPACE / "no_such_dir_marker"))
    result = _run_probe(
        monkeypatch, enabled=True,
        code="import sys; print(any('no_such_dir_marker' in p for p in sys.path))",
    )
    assert (result.get("stdout") or "").strip() == "False"


def test_the_report_says_whether_the_workspace_was_visible(monkeypatch):
    """Невидимый отказ не должен выглядеть как настоящий ноль (открытая запись
    агента severity=high): отчёт пробы называет, видела ли она модули."""
    assert _run_probe(monkeypatch, enabled=True)["workspace_import"] is True
    off = _run_probe(monkeypatch, enabled=False)
    assert off["workspace_import"] is False
    assert _SWITCH in off.get("note", "")


def test_a_traceback_keeps_the_line_numbers_of_the_experiment(monkeypatch):
    """Путь вставляется так, что номера строк в ошибке — строки самого опыта."""
    result = _run_probe(monkeypatch, enabled=True,
                        code="x = 1\ny = 2\nraise ValueError('line three')")
    assert 'line 3' in (result.get("stderr") or "")
