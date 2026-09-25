"""«Воспроизведено до правки» — одно правило для самопочинки и для свидетеля (25.09).

Было два: MIR-110 (`_diagnosis_verified`) требовал, чтобы упавший тест был
назван диагнозом; свидетель patch_route/patch_check гонял новый тест на старом
коде, но верил любому ненулевому коду выхода — и пустой тестовый файл (pytest
выходит с 5, «тестов нет») проходил как свидетель.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.patch_route import _FORBIDDEN
from core.self_apply_lane import PROTECTED_CORE
from core.self_repair_utils import _diagnosis_verified, red_before_fix
from tools.patch_check import _verdict


def test_a_failure_or_a_collection_error_on_old_code_is_red() -> None:
    assert red_before_fix({"timed_out": False, "exit_code": 1})
    assert red_before_fix({"timed_out": False, "exit_code": 2})  # импорт того, чего до правки нет


def test_no_tests_usage_or_internal_errors_are_not_a_witness() -> None:
    for code in (0, 3, 4, 5, None):
        assert not red_before_fix({"timed_out": False, "exit_code": code}), code
    assert not red_before_fix({"timed_out": True, "exit_code": 1})


def test_the_diagnosis_rule_is_the_same_rule_and_still_needs_the_name() -> None:
    run = {"timed_out": False, "exit_code": 1, "failed": 1, "errors": 0,
           "failed_tests": ["tests/test_parser.py::test_empty_line"]}
    named = SimpleNamespace(test_pattern="", test_paths=(), reason="test_empty_line fails", evidence=())
    other = SimpleNamespace(test_pattern="", test_paths=(), reason="the cache is stale", evidence=())
    assert _diagnosis_verified(run, named) and red_before_fix(run, named)
    assert not _diagnosis_verified(run, other)
    assert not _diagnosis_verified({**run, "exit_code": 5, "failed": 0, "failed_tests": []})


def test_patch_check_refuses_a_witness_that_collected_nothing() -> None:
    base = {"diff": "x", "tests_exit_code": 0, "full_exit_code": 0}
    assert _verdict({**base, "witness_exit_code": 5})["verdict"] == "red"
    assert _verdict({**base, "witness_exit_code": 1})["verdict"] == "green"
    assert _verdict({**base, "witness_exit_code": 0})["verdict"] == "red"


def test_the_agent_cannot_loosen_the_rule_by_its_own_patch() -> None:
    assert "core/self_repair_utils.py" in PROTECTED_CORE
    assert any("core/self_repair_utils.py".startswith(p) for p in _FORBIDDEN)
