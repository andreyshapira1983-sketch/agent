"""Лаборатория: вердикт о своей среде меряется экспериментом, а не выводится.

Background: docs/CODE_NOTES.md, "The verdict was inferred, the lab was locked".
"""
from __future__ import annotations

import sys

import pytest

from tools.python_probe import PythonProbeTool


def _run(code: str, **kw):
    return PythonProbeTool().run(code=code, **kw)


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


def test_an_endless_experiment_is_cut_by_the_timeout():
    out = _run("while True: pass", timeout_seconds=1)

    assert out["timed_out"] is True
    assert PythonProbeTool().execution_status(out) == "failed"


def test_output_is_capped():
    out = _run("print('x' * 200000)")

    assert out["stdout_truncated"] is True
    assert len(out["stdout"]) < 200000


def test_the_doorman_still_rejects_shapeless_lab_steps():
    from core.step_sanitizer import sanitize_step

    for bad in ({}, {"code": ""}, {"code": "x" * 5000},
                {"code": "print(1)", "timeout_seconds": 999}):
        warnings: list[str] = []
        assert sanitize_step("python_probe", bad, None, 0, warnings) is None
        assert warnings, bad
