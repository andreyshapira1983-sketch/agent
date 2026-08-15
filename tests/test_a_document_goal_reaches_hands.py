"""Документная цель получает руки: выбиратель видит цель, черновик едет в очередь.

Background: docs/CODE_NOTES.md, "The head chose, the hands didn't know how".
"""
from __future__ import annotations

from types import SimpleNamespace

from core.best_next_action import doc_target_from_goal, select_best_next_action
from core.campaign_io import _propose_doctrine_draft

#: Живая цель первой хартийной кампании 2026-08-15 — выбрана самим агентом.
_DOC_GOAL = (
    "Draft a proposal for the structure and rules of the "
    "'MEMORY_LIFECYCLE_CONTRACT.md' document, focusing on admission, "
    "retrieval, and retention policies for per-agent memory."
)
_OPEN_ISSUE = {
    "fingerprint": "abc123",
    "status": "open",
    "action": "improve_failure_to_idea_pipeline",
    "title": "Resolve the open self-improvement issue",
    "evidence": ["e1"],
}


def test_a_bare_document_name_lands_in_doctrine_future():
    assert doc_target_from_goal(_DOC_GOAL) == (
        "knowledge/doctrine/future/MEMORY_LIFECYCLE_CONTRACT.md"
    )


def test_an_explicit_path_is_kept():
    goal = "Напиши черновик docs/MEMORY_LIFECYCLE_CONTRACT.md по хартии"
    assert doc_target_from_goal(goal) == "docs/MEMORY_LIFECYCLE_CONTRACT.md"


def test_a_goal_without_a_document_names_no_target():
    assert doc_target_from_goal(
        "Найди в своём собственном коде конкретный дефект и почини"
    ) == ""


def test_a_document_goal_beats_the_repair_habit():
    """Живой замер 2026-08-15: голова выбрала «напиши контракт», руки сделали
    привычный ремонт (improve_failure_to_idea_pipeline, priority 55), потому
    что в каталоге действий не было документного. Документная цель обязана
    побеждать привычку — но не аварии здоровья (60+).
    """
    action = select_best_next_action(
        goal=_DOC_GOAL,
        open_self_improvement_issues=(_OPEN_ISSUE,),
        self_improvement_registry_available=True,
    )

    assert action.action == "draft_doctrine_document"
    assert "MEMORY_LIFECYCLE_CONTRACT.md" in " ".join(action.evidence)


def test_health_emergencies_still_outrank_the_document():
    action = select_best_next_action(
        goal=_DOC_GOAL,
        tests_health="fail",
        failed_tests=("tests/test_x.py::test_a",),
    )

    assert action.action == "propose_minimal_test_repair"


def test_a_repair_goal_keeps_the_habitual_pick():
    """Улов не отдан: без документной цели выбор прежний."""
    action = select_best_next_action(
        goal="Найди в своём собственном коде конкретный дефект и почини",
        open_self_improvement_issues=(_OPEN_ISSUE,),
        self_improvement_registry_available=True,
    )

    assert action.action == "improve_failure_to_idea_pipeline"


class _Inbox:
    def __init__(self) -> None:
        self.items: list = []

    def add(self, **kw):
        self.items.append(kw)
        return SimpleNamespace(id="ain_doc_draft")


class _DraftLLM:
    def complete(self, **_kw) -> str:
        return (
            "# Memory Lifecycle Contract — DRAFT\n\n"
            "> STATUS: DRAFT / TARGET, not implemented.\n\n"
            "## Admission\nEvery record carries provenance.\n"
        )


def _agent(reply_llm=None):
    return SimpleNamespace(llm=reply_llm or _DraftLLM(), log=None)


def test_a_draft_becomes_an_approval_item_not_a_file(tmp_path):
    """Черновик не пишется на диск сам: он едет заявкой self_apply_lane.run —
    решение человека, лента с полным сьютом и откатом.
    """
    inbox = _Inbox()

    note = _propose_doctrine_draft(
        agent=_agent(), workspace=tmp_path, goal=_DOC_GOAL, approval_inbox=inbox,
    )

    assert note == "doc_draft_proposed:ain_doc_draft"
    (item,) = inbox.items
    assert item["operation"] == "self_apply_lane.run"
    (file,) = item["payload"]["files"]
    assert file["path"] == "knowledge/doctrine/future/MEMORY_LIFECYCLE_CONTRACT.md"
    assert "DRAFT" in file["content"]
    target = tmp_path / "knowledge" / "doctrine" / "future" / "MEMORY_LIFECYCLE_CONTRACT.md"
    assert not target.exists(), "на диск без человека не пишется ничего"


def test_a_fence_wrapped_generation_is_unwrapped(tmp_path):
    """Живой грех первого черновика (ain_848a6f8f): модель обернула документ
    в ```md-ограду вопреки инструкции, и ограда ехала в файл. Одна внешняя
    ограда снимается; ограды ВНУТРИ документа не трогаются.
    """
    class _Fenced:
        def complete(self, **_kw) -> str:
            return (
                "```md\n# Doc — DRAFT\n\nA code sample:\n"
                "```python\nx = 1\n```\n\nTail.\n```"
            )

    inbox = _Inbox()
    note = _propose_doctrine_draft(
        agent=_agent(_Fenced()), workspace=tmp_path, goal=_DOC_GOAL,
        approval_inbox=inbox,
    )

    assert note.startswith("doc_draft_proposed")
    (file,) = inbox.items[0]["payload"]["files"]
    assert file["content"].startswith("# Doc — DRAFT")
    assert "```python" in file["content"], "внутренние ограды не тронуты"
    assert file["content"].rstrip().endswith("Tail.")


def test_an_existing_document_is_not_overwritten(tmp_path):
    target = tmp_path / "knowledge" / "doctrine" / "future" / "MEMORY_LIFECYCLE_CONTRACT.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("already here", encoding="utf-8")

    note = _propose_doctrine_draft(
        agent=_agent(), workspace=tmp_path, goal=_DOC_GOAL, approval_inbox=_Inbox(),
    )

    assert note == "doc_declined:doc_exists"


def test_an_empty_generation_is_declined(tmp_path):
    class _Empty:
        def complete(self, **_kw) -> str:
            return "   "

    note = _propose_doctrine_draft(
        agent=_agent(_Empty()), workspace=tmp_path, goal=_DOC_GOAL,
        approval_inbox=_Inbox(),
    )

    assert note == "doc_declined:empty_draft"


def test_a_goal_without_a_target_is_declined(tmp_path):
    note = _propose_doctrine_draft(
        agent=_agent(), workspace=tmp_path, goal="почини дефект",
        approval_inbox=_Inbox(),
    )

    assert note == "doc_declined:no_target_doc"
