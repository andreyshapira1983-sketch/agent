"""Дефект без красного теста сперва зарабатывает тест — ветка А.

Background: docs/CODE_NOTES.md, "A diagnosis earns a test, not a patch".
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from core.campaign_io import _propose_failing_test_from_diagnosis
from core.self_task_producer import _task_builder_generate, produce_coding_task

_ANSWER = (
    "Зафиксирован reasoning_action_mismatch: выполнен list_dir без плана; "
    "место проверки — core/reasoning_action_check.py."
)


class _JsonLLM:
    """Возвращает валидный ответ автора задач и запоминает подсказку."""

    def __init__(self) -> None:
        self.system = ""
        self.user = ""

    def complete(self, *, system: str, user: str, **_kw) -> str:
        self.system, self.user = system, user
        return json.dumps({
            "task_title": "Reproduce the mismatch",
            "task_summary": "Add a failing test that reproduces the defect.",
            "impl_path": "core/reasoning_action_check.py",
            "test_path": "tests/test_reproduce_mismatch.py",
            "test_lines": [
                "from core.reasoning_action_check import check",
                "def test_unplanned_tool_is_flagged():",
                "    assert check(['list_dir'], planned=[]) == ['list_dir']",
            ],
            "confidence": 0.9,
        })


class _Inbox:
    def __init__(self) -> None:
        self.items: list = []

    def list(self):
        return []

    def add(self, **kw):
        self.items.append(kw)
        return SimpleNamespace(id="ain_stageA")


def _candidate():
    return SimpleNamespace(
        target_path="core/reasoning_action_check.py",
        problem_quote=_ANSWER,
        evidence_ref="verified_diagnosis:trace_x",
    )


def test_the_frame_names_the_evidence_for_what_it_is():
    """Скармливать диагноз под видом «TODO-комментария» значит врать модели —
    рамка обязана называть улику своим именем.
    """
    llm = _JsonLLM()

    _task_builder_generate(
        llm, impl_path="core/reasoning_action_check.py", quote=_ANSWER,
        evidence_ref="verified_diagnosis:trace_x", current_content="",
        source_kind="verified_diagnosis",
    )

    assert "VERIFIED self-diagnosis" in llm.system
    assert "REPRODUCE the diagnosed defect" in llm.system
    assert "TODO/FIXME comment" not in llm.system
    assert "Verified diagnosis:" in llm.user


def test_the_erased_todo_frame_stays_gone():
    """Перепремировано 2026-08-28 (MIR-183): до стирания code_todo этот тест
    закреплял ОБРАТНОЕ — «прежний источник говорит прежними словами». Теперь
    кадра TODO не существует, и дефолтный кадр называет уликой само-аудит."""
    llm = _JsonLLM()

    _task_builder_generate(
        llm, impl_path="cli/x.py", quote="audit gap: do it",
        evidence_ref="cli/x.py:1", current_content="",
    )

    assert "TODO/FIXME comment" not in llm.system
    assert "PRIORITY GAP" in llm.system


def test_a_diagnosis_candidate_becomes_a_blessed_test_proposal(tmp_path):
    """Полный Stage A на диагнозном кандидате: заявка `self_build_task.approve`
    с замороженным падающим тестом ждёт человека — реализации ещё нет.
    (Полигон временный: продюсер теперь пишет квитанции впрыска уроков, и
    workspace=\".\" оставлял бы тестовые строки в живых журналах.)
    """
    inbox = _Inbox()
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "reasoning_action_check.py").write_text(
        "def check(executed, planned):\n    return executed\n",
        encoding="utf-8")
    (tmp_path / "tests").mkdir()

    report = produce_coding_task(
        workspace=tmp_path, inbox=inbox, llm=_JsonLLM(),
        task_selector=_candidate, source_kind="verified_diagnosis",
    )

    assert report.status == "proposed"
    assert len(inbox.items) == 1
    item = inbox.items[0]
    assert item["operation"] == "self_build_task.approve"
    assert any("verified_diagnosis:" in str(e) for e in item["payload"]["evidence"])


def test_the_wire_routes_no_failing_tests_into_stage_a(monkeypatch):
    """Провод: отказ ремонтника «no_failing_tests» не конец, а пересадка."""
    import core.self_task_producer as stp

    calls = {}

    def _fake_produce(**kw):
        calls.update(kw)
        return SimpleNamespace(status="proposed", approval_id="ain_stageA")

    monkeypatch.setattr(stp, "produce_coding_task", _fake_produce)

    note = _propose_failing_test_from_diagnosis(
        agent=SimpleNamespace(llm=None, log=None), workspace=".",
        target="core/reasoning_action_check.py", answer=_ANSWER,
        approval_inbox=_Inbox(),
    )

    assert note == "test_proposed:ain_stageA"
    assert calls["source_kind"] == "verified_diagnosis"
    assert calls["task_selector"]().target_path == "core/reasoning_action_check.py"


def test_a_stage_a_refusal_is_surfaced_not_hidden(monkeypatch):
    """Гейты Stage A действуют без изъятий, и их отказ виден оператору."""
    import core.self_task_producer as stp

    monkeypatch.setattr(
        stp, "produce_coding_task",
        lambda **kw: SimpleNamespace(status="dirty_tree_wait", reason="dirty"),
    )

    note = _propose_failing_test_from_diagnosis(
        agent=SimpleNamespace(llm=None, log=None), workspace=".",
        target="core/x.py", answer=_ANSWER, approval_inbox=_Inbox(),
    )

    assert note == "test_declined:dirty_tree_wait"
