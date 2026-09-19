"""Лаборатория: вердикт о своей среде меряется экспериментом, а не выводится.

Background: docs/CODE_NOTES.md, "The verdict was inferred, the lab was locked".
"""
from __future__ import annotations

import sys

import pytest

from tools.python_probe import PythonProbeTool


def _run(code: str, **kw):
    return PythonProbeTool().run(code=code, **kw)


def test_the_runtime_version_is_measured_not_guessed():
    out = _run("import sys; print('.'.join(map(str, sys.version_info[:3])))")

    expected = ".".join(map(str, sys.version_info[:3]))
    assert out["exit_code"] == 0
    assert expected in out["stdout"]


def test_a_failing_import_is_a_successful_measurement():
    """Живая проба 2026-08-16: вердикт «batched здесь не работает» был выведен,
    а не измерен. Эксперимент, который падает, — УДАВШИЙСЯ замер: ImportError
    и есть данные.
    """
    out = _run("from itertools import batched")

    if sys.version_info >= (3, 12):
        pytest.skip("на 3.12+ этот замер даёт другой исход")
    assert out["exit_code"] != 0
    assert "ImportError" in out["stderr"] or "cannot import" in out["stderr"]
    assert PythonProbeTool().execution_status(out) == "success"


def test_api_keys_do_not_reach_the_experiment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-2")

    out = _run("import os; print(repr(os.environ.get('ANTHROPIC_API_KEY')), "
               "repr(os.environ.get('OPENAI_API_KEY')))")

    assert "sk-test" not in out["stdout"]
    assert "None" in out["stdout"]


@pytest.mark.parametrize("code,marker", [
    ("import subprocess", "subprocess"),
    ("from shutil import rmtree", "shutil"),
    ("import socket", "socket"),
    ("import ctypes", "ctypes"),
    ("__import__('subprocess')", "__import__"),
    ("open('x.txt', 'w')", "open"),
    ("import os\nos.remove('x')", "os.remove"),
])
def test_effectful_shapes_are_refused_before_execution(code, marker):
    """Лаборатория меряет, а не действует: процессы, сеть, массовые удаления
    и запись отклоняются структурно, с названной причиной, ДО запуска.
    """
    with pytest.raises(ValueError, match=marker.replace(".", r"\.")):
        _run(code)


def test_read_open_and_plain_os_are_not_refused():
    """Улов не отдан: чтение файла и безобидный os — законные замеры."""
    out = _run("import os; print(os.name); open('no_such_file')")

    assert out["exit_code"] != 0  # FileNotFoundError — это замер, не отказ гейта


def test_an_endless_experiment_is_cut_by_the_timeout():
    out = _run("while True: pass", timeout_seconds=1)

    assert out["timed_out"] is True
    assert PythonProbeTool().execution_status(out) == "failed"


def test_output_is_capped():
    out = _run("print('x' * 200000)")

    assert out["stdout_truncated"] is True
    assert len(out["stdout"]) < 200000


def test_the_lab_is_wired_into_the_default_registry():
    import pathlib

    src = (pathlib.Path("app") / "bootstrap.py").read_text(encoding="utf-8")
    assert "PythonProbeTool(" in src


def test_the_unattended_path_opens_the_lab_by_the_operators_word():
    """Отдельное решение, которого ждала лаборатория, принято: 2026-09-19, слово оператора перед суточным прогоном: «разрешения у него будут все»."""
    from core.autonomous_runtime import _AUTONOMOUS_GOAL_BLOCKED_TOOLS

    assert "python_probe" not in _AUTONOMOUS_GOAL_BLOCKED_TOOLS


def test_the_doorman_admits_a_lab_step():
    """Проба №3 (2026-08-16): планировщик, выучив карту, СПЛАНИРОВАЛ
    эксперимент — и швейцар выбросил шаг: «tool 'python_probe' has no
    sanitiser». У каждой руки должен быть свой пропуск.
    """
    from core.step_sanitizer import sanitize_step

    warnings: list[str] = []
    step = sanitize_step(
        "python_probe", {"code": "from itertools import batched"},
        None, 0, warnings,
    )

    assert step is not None, warnings
    assert step["tool"] == "python_probe"
    assert step["arguments"]["code"] == "from itertools import batched"


def test_the_doorman_still_rejects_shapeless_lab_steps():
    from core.step_sanitizer import sanitize_step

    for bad in ({}, {"code": ""}, {"code": "x" * 5000},
                {"code": "print(1)", "timeout_seconds": 999}):
        warnings: list[str] = []
        assert sanitize_step("python_probe", bad, None, 0, warnings) is None
        assert warnings, bad
