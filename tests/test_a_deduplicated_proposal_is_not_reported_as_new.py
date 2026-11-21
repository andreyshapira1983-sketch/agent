"""Схлопнутая заявка не должна записываться как поданная.

Замер, отвергнутые варианты и границы: F-1 в docs/audit/FIELD_CHECK_QUEUE.md.
"""
from __future__ import annotations

import pathlib

from core.approval_inbox import ApprovalInbox


def _inbox(tmp_path: pathlib.Path) -> ApprovalInbox:
    return ApprovalInbox(path=tmp_path / "inbox.jsonl")


def test_the_live_doctrine_path_returns_superseded_not_proposed(tmp_path) -> None:
    """Проводка, а не наличие: помощник обязан СТОЯТЬ на живом пути.

    Тест на функцию зелен и тогда, когда её никто не зовёт — сегодня этот урок
    повторился четырежды. Здесь гоняется сам путь кампании, с поддельной
    моделью: предмет проверки — отчёт о столкновении, а не текст черновика.
    """
    import core.campaign_io as mod

    goal = "draft docs/CODE_NOTES.md"
    target = "docs/CODE_NOTES.md"
    inbox = _inbox(tmp_path)
    inbox.add(
        operation="self_apply_lane.run", summary="уже лежит", risk="reversible",
        reasons=("ранее",),
        payload={"files": [{"path": target, "content": "СТАРОЕ"}]},
        dedup_key=f"self_apply:{target}:campaign_doctrine_draft",
    )

    class _Llm:
        def complete(self, **_kw) -> str:
            return (
                "# STATUS: DRAFT / TARGET" + chr(10) * 2
                + "совсем другой черновик" + chr(10)
            )

    class _Log:
        def __init__(self) -> None:
            self.events: list[str] = []

        def log(self, event, payload=None, **extra) -> None:
            self.events.append(event)

    class _Agent:
        def __init__(self) -> None:
            self.llm = _Llm()
            self.log = _Log()

    agent = _Agent()
    verdict = mod._propose_doctrine_draft(
        agent=agent, workspace=tmp_path, goal=goal, approval_inbox=inbox,
    )

    # A5 (блок 1, 2026-09-03): столкновение — не предложение. Журнал говорит
    # «superseded» (F-1), а исход цикла — «ничего не подано»: раньше строка
    # `doc_draft_superseded…` возвращалась как proposal и делала цикл полезным.
    assert verdict is None, (
        "живой путь по-прежнему называет схлопнутую заявку поданной: " + str(verdict)
    )
    assert "campaign_doc_draft_superseded" in agent.log.events, agent.log.events
    assert "campaign_doc_draft_proposed" not in agent.log.events, agent.log.events
