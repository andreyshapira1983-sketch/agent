"""Схлопнутая заявка не должна записываться как поданная.

Замер, отвергнутые варианты и границы: F-1 в docs/audit/FIELD_CHECK_QUEUE.md.
"""
from __future__ import annotations

import pathlib

import pytest

from core.approval_inbox import ApprovalInbox

_KEY = "self_apply:docs/x.md:campaign_doctrine_draft"


def _inbox(tmp_path: pathlib.Path) -> ApprovalInbox:
    return ApprovalInbox(path=tmp_path / "inbox.jsonl")


def _add(inbox: ApprovalInbox, *, summary: str, content: str):
    return inbox.add(
        operation="self_apply_lane.run",
        summary=summary,
        risk="reversible",
        reasons=("основание",),
        payload={"files": [{"path": "docs/x.md", "content": content}]},
        dedup_key=_KEY,
    )


def test_a_collision_is_visible_to_the_caller(tmp_path) -> None:
    """Вызывающий обязан МОЧЬ узнать, что его заявка схлопнулась."""
    inbox = _inbox(tmp_path)
    _add(inbox, summary="черновик A", content="СОДЕРЖИМОЕ А")

    assert inbox.find_pending_by_dedup_key(_KEY) is not None, (
        "узнать о столкновении нечем — вызывающему остаётся только верить, "
        "что его заявка подана"
    )


def test_an_absent_key_reads_absent(tmp_path) -> None:
    """Контроль: без него первый тест проходил бы и на функции, всегда что-то возвращающей."""
    inbox = _inbox(tmp_path)
    assert inbox.find_pending_by_dedup_key("self_apply:docs/нет.md:x") is None


def test_the_collision_really_keeps_the_first_content(tmp_path) -> None:
    """Замер, на котором стоит вся запись: побеждает первый, второй исчезает."""
    inbox = _inbox(tmp_path)
    first = _add(inbox, summary="черновик A", content="СОДЕРЖИМОЕ А")
    second = _add(inbox, summary="черновик Б", content="СОВСЕМ ДРУГОЕ Б")

    assert first.id == second.id
    pending = inbox.pending()
    assert len(pending) == 1
    kept = pending[0].payload["files"][0]["content"]
    assert kept == "СОДЕРЖИМОЕ А", (
        "поведение изменилось — теперь побеждает последний; это TOCTOU на "
        "заявке, которую человек мог уже читать, и запись надо переписать"
    )


@pytest.mark.parametrize("origin", ["campaign_doctrine_draft", "campaign_diagnosis"])
def test_the_campaign_does_not_claim_a_proposal_it_did_not_make(
    tmp_path, origin: str,
) -> None:
    """Живой путь: строка, которую кампания возвращает и пишет в журнал."""
    import core.campaign_io as mod

    inbox = _inbox(tmp_path)
    key = f"self_apply:docs/x.md:{origin}"
    inbox.add(
        operation="self_apply_lane.run",
        summary="уже лежит",
        risk="reversible",
        reasons=("ранее",),
        payload={"files": [{"path": "docs/x.md", "content": "СТАРОЕ"}]},
        dedup_key=key,
    )

    verdict = mod._dedup_verdict(inbox, key)

    assert verdict is not None, (
        "кампания не спрашивает ящик о столкновении и потому не может "
        "отличить поданную заявку от схлопнутой"
    )
    assert "superseded" in verdict or "existing" in verdict, verdict

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

    assert verdict is not None
    assert "superseded" in verdict, (
        "живой путь по-прежнему называет схлопнутую заявку поданной: " + verdict
    )
    assert "campaign_doc_draft_proposed" not in agent.log.events, agent.log.events
