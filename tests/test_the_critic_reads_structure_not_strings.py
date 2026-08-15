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

#: Три греха живой заявки ain_31874b06 (2026-08-15, отклонена оператором),
#: сведённые к их сути. Каждый прошёл строковое сито.
_TAUTOLOGY = "assert report.status in {'failed'} or report.status == report.status"
_PHANTOM = (
    "from core.self_repair_models import RepairProposal\n"
    "p = RepairProposal(path='x', proposed_content='y',\n"
    "                   test_files=('t.py',), evidence_verified=False)\n"
)
_DIAGNOSIS = (
    "Зафиксирован reasoning_action_mismatch: запланирован web_search, "
    "выполнен list_dir; обвязка в core/loop.py."
)


def test_the_measured_tautology_is_named():
    reason = _vacuous_assert_reason(ast.parse(_TAUTOLOGY))

    assert reason is not None
    assert "always true" in reason


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


def test_the_measured_phantom_kwargs_are_named():
    """`RepairProposal(test_files=…)` при конструкторе без такого поля падает
    TypeError навсегда — у теста нет зелёного состояния.
    """
    reason = _phantom_kwargs_reason(ast.parse(_PHANTOM))

    assert reason is not None
    assert "test_files" in reason


def test_real_kwargs_pass_the_signature_check():
    src = (
        "from core.self_repair_models import RepairProposal\n"
        "p = RepairProposal(path='x', proposed_content='y', confidence=0.5)\n"
    )
    assert _phantom_kwargs_reason(ast.parse(src)) is None


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


def test_a_test_that_names_the_defect_passes_linkage():
    linked = (
        "def test_mismatch_flagged():\n"
        "    assert 'reasoning_action_mismatch' in flags(['list_dir'], [])\n"
    )
    assert _diagnosis_linkage_reason(linked, _DIAGNOSIS) is None


def test_a_quote_with_no_carriers_cannot_judge():
    """Диагнозу без кодовых имён нечем связаться — судить не о чем."""
    assert _diagnosis_linkage_reason("def test_a(): assert f(1) == 2", "всё сломалось") is None


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


def test_a_sound_diagnosis_test_still_passes_the_critic():
    """Ломка наоборот: честный воспроизводящий тест проходит."""
    critic = _task_critic_review(
        _build([
            "from core.reasoning_action_check import check",
            "def test_reasoning_action_mismatch_flags_unplanned_tool():",
            "    assert check(executed=['list_dir'], planned=[]) == ['list_dir']",
        ]),
        grounded_target="core/reasoning_action_check.py",
        reader=lambda _p: None,
        confidence_threshold=0.6,
        quote=_DIAGNOSIS,
        source_kind="verified_diagnosis",
    )

    assert critic.decision != "veto", critic.data.get("veto_reasons")


def test_code_todo_source_is_not_asked_for_linkage():
    """У TODO-источника связь и так обеспечена целевым файлом; спрашивать с
    него носители диагноза значило бы ветировать законные задачи.
    """
    critic = _task_critic_review(
        _build([
            "from core.reasoning_action_check import check",
            "def test_todo_done():",
            "    assert check(executed=[], planned=[]) == []",
        ]),
        grounded_target="core/reasoning_action_check.py",
        reader=lambda _p: None,
        confidence_threshold=0.6,
        quote="# TODO: handle the empty plan",
    )

    assert critic.decision != "veto", critic.data.get("veto_reasons")
