"""Дефект без красного теста сперва зарабатывает тест — ветка А.

Background: docs/CODE_NOTES.md, "A diagnosis earns a test, not a patch".
"""
from __future__ import annotations

from types import SimpleNamespace

from core.campaign_io import _propose_failing_test_from_diagnosis

_ANSWER = (
    "Зафиксирован reasoning_action_mismatch: выполнен list_dir без плана; "
    "место проверки — core/reasoning_action_check.py."
)


class _Inbox:
    def __init__(self) -> None:
        self.items: list = []

    def list(self):
        return []

    def add(self, **kw):
        self.items.append(kw)
        return SimpleNamespace(id="ain_stageA")


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
