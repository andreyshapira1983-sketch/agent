"""Документная цель получает руки: выбиратель видит цель, черновик едет в очередь.

Background: docs/CODE_NOTES.md, "The head chose, the hands didn't know how".
"""
from __future__ import annotations

from types import SimpleNamespace

from core.campaign_io import _propose_doctrine_draft

#: Живая цель первой хартийной кампании 2026-08-15 — выбрана самим агентом.
_DOC_GOAL = (
    "Draft a proposal for the structure and rules of the "
    "'MEMORY_LIFECYCLE_CONTRACT.md' document, focusing on admission, "
    "retrieval, and retention policies for per-agent memory."
)


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


def test_a_draft_bannered_file_may_be_filled(tmp_path):
    """Скелет → протокол (команда оператора 2026-08-15): файл, сам несущий
    баннер DRAFT, разрешено наполнять; текущий скелет уезжает модели в
    подсказку, чтобы наполнение шло по его же структуре.
    """
    target = tmp_path / "knowledge" / "doctrine" / "future" / "MEMORY_LIFECYCLE_CONTRACT.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# STATUS: DRAFT / TARGET (not implemented)\n\n## Required sections\n",
        encoding="utf-8",
    )

    class _Capturing:
        def __init__(self) -> None:
            self.system = ""
            self.user = ""

        def complete(self, *, system: str, user: str, **_kw) -> str:
            self.system, self.user = system, user
            return "# STATUS: DRAFT / TARGET\n\nFilled protocol.\n"

    llm = _Capturing()
    inbox = _Inbox()

    note = _propose_doctrine_draft(
        agent=_agent(llm), workspace=tmp_path, goal=_DOC_GOAL,
        approval_inbox=inbox,
    )

    assert note == "doc_draft_proposed:ain_doc_draft"
    assert "Required sections" in llm.user, "скелет показан модели"
    assert "do NOT restate" in llm.system, (
        "живой замер ain_2edf62f4: без запрета пересказа модель вернула "
        "скелет с 89% дословных совпадений вместо содержания"
    )
    (file,) = inbox.items[0]["payload"]["files"]
    assert "Filled protocol" in file["content"]


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
