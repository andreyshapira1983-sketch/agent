"""«Воспроизведено до правки» — одно правило для самопочинки и для свидетеля (25.09).

Было два: MIR-110 (`_diagnosis_verified`) требовал, чтобы упавший тест был
назван диагнозом; свидетель patch_route/patch_check гонял новый тест на старом
коде, но верил любому ненулевому коду выхода — и пустой тестовый файл (pytest
выходит с 5, «тестов нет») проходил как свидетель.
"""
from __future__ import annotations

from core.self_repair_utils import red_before_fix
from tools.patch_check import _verdict


def test_no_tests_usage_or_internal_errors_are_not_a_witness() -> None:
    for code in (0, 3, 4, 5, None):
        assert not red_before_fix({"timed_out": False, "exit_code": code}), code
    assert not red_before_fix({"timed_out": True, "exit_code": 1})


def test_patch_check_refuses_a_witness_that_collected_nothing() -> None:
    base = {"diff": "x", "tests_exit_code": 0, "full_exit_code": 0}
    assert _verdict({**base, "witness_exit_code": 5})["verdict"] == "red"
    assert _verdict({**base, "witness_exit_code": 1})["verdict"] == "green"
    assert _verdict({**base, "witness_exit_code": 0})["verdict"] == "red"
