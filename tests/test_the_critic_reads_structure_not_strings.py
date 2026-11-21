"""Критик Stage A судит по AST и настоящим сигнатурам, а не по строкам.

Background: docs/CODE_NOTES.md, "The critic that read strings".
"""
from __future__ import annotations

import ast

import pytest

from core.self_task_producer import (
    _diagnosis_linkage_reason,
    _phantom_kwargs_reason,
    _task_critic_review,
    _vacuous_assert_reason,
)

_DIAGNOSIS = (
    "Зафиксирован reasoning_action_mismatch: запланирован web_search, "
    "выполнен list_dir; обвязка в core/loop.py."
)


@pytest.mark.parametrize("src", [
    "assert True",
    "assert x == x",
    "assert n >= n",
    "assert key in key",
    "assert a == b or c == c",
])
def test_unseen_tautology_shapes_are_caught(src: str):
    """Формы, под которые правило не подгоняли: рефлексивные сравнения и
    Or с всегда-истинным операндом. Класс один — линейка без делений.
    """
    assert _vacuous_assert_reason(ast.parse(src)) is not None


@pytest.mark.parametrize("src", [
    "assert x == y",
    "assert report.status == 'failed'",
    "assert a or b",
    "assert x in allowed",
])
def test_real_asserts_are_untouched(src: str):
    """Улов не отдан: настоящее сравнение двух разных вещей — не тавтология."""
    assert _vacuous_assert_reason(ast.parse(src)) is None


@pytest.mark.parametrize("src", [
    "from types import SimpleNamespace\nSimpleNamespace(anything_goes=1)\n",
    "from nonexistent_module_xyz import Thing\nThing(bad_kwarg=1)\n",
    "import json\njson.dumps({'a': 1}, indent=2)\n",
])
def test_doubt_means_silence(src: str):
    """**kwargs, неимпортируемое, атрибутные вызовы — сомнение, и сито молчит:
    оно только вычитает мусор, никогда не блокирует на неуверенности.
    """
    assert _phantom_kwargs_reason(ast.parse(src)) is None


def test_a_test_about_nothing_from_the_diagnosis_is_named():
    """Живой тест был про выдуманный пробел в self_repair, диагноз — про
    reasoning_action_mismatch. Ни одного носителя диагноза в тесте.
    """
    off_topic = "def test_x():\n    assert make_report().error == 'a; b'\n"

    reason = _diagnosis_linkage_reason(off_topic, _DIAGNOSIS)

    assert reason is not None
    assert "reasoning_action_mismatch" in reason


def _build(test_lines: list[str]) -> dict:
    return {
        "impl_path": "core/reasoning_action_check.py",
        "test_path": "tests/test_new_repro.py",
        "test_content": "\n".join(test_lines) + "\n",
        "task_title": "t",
        "confidence": 0.9,
    }


def test_the_critic_now_vetoes_the_live_shape():
    """Сквозь критика целиком: суть отклонённой заявки больше не доходит до
    человека — сито само называет все грехи.
    """
    critic = _task_critic_review(
        _build([
            "from core.reasoning_action_check import check",
            "from core.self_repair_models import RepairProposal",
            "def test_x():",
            "    p = RepairProposal(path='x', proposed_content='y', test_files=())",
            "    assert p.path in {'x'} or p.path == p.path",
        ]),
        grounded_target="core/reasoning_action_check.py",
        reader=lambda _p: None,
        confidence_threshold=0.6,
        quote=_DIAGNOSIS,
        source_kind="verified_diagnosis",
    )

    assert critic.decision == "veto"
    reasons = " | ".join(critic.data.get("veto_reasons", []))
    assert "always true" in reasons
    assert "test_files" in reasons
    assert "reasoning_action_mismatch" in reasons
