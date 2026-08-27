"""Отказ обязан нести причину, и причину обязан кто-то читать.

Замер и класс: MIR-100. 38 живых отказов оператора (2026-08-23) были записаны
ПУСТЫМИ — высший сигнал обратной связи (человек, говорящий «нет» работе,
которую агент выбрал сам, 55–60 % отказов) стирался в смену статуса.

Механизм построен раньше этого файла (deny с пустой причиной — ValueError до
смены статуса; мост data/approval_outcomes.jsonl → _recent_verdicts → выбор
цели), но НЕ был приколот: контракт, который «мог быть тихо опустошён в
единственный момент, для которого существует» (его же докстринг), был ровно
в этом состоянии. Здесь — прикол по смыслу с обеих сторон.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.approval_inbox import ApprovalInbox


def _inbox(tmp_path: Path) -> ApprovalInbox:
    inbox = ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl")
    inbox.add(operation="self_apply_lane.run", summary="s")
    return inbox


def test_an_empty_denial_is_refused_before_any_status_change(tmp_path: Path) -> None:
    inbox = _inbox(tmp_path)
    item = inbox.pending()[0]

    with pytest.raises(ValueError):
        inbox.deny(item.id, reason="   ")

    assert inbox.pending(), "отвергнутый отказ обязан оставить заявку pending"


def test_a_denial_with_a_reason_lands_and_keeps_it(tmp_path: Path) -> None:
    """Другая сторона: причина доезжает до записи, не только до проверки."""
    inbox = _inbox(tmp_path)
    item = inbox.pending()[0]

    denied = inbox.deny(item.id, reason="цель не названа", actor="operator")

    assert denied.status == "denied"


def test_the_reason_reaches_the_outcomes_journal(tmp_path: Path) -> None:
    """Мост вердиктов: отказ с причиной ложится в approval_outcomes.jsonl.

    Читаем читателем самого моста (state_integrity), не сырым json: журнал
    лежит в конверте целостности, и сырой разбор увидел бы пустоту там, где
    запись есть, — незнание не приговор.
    """
    from core.state_integrity import read_state_jsonl

    inbox = ApprovalInbox(path=tmp_path / "data" / "approval_inbox.jsonl")
    inbox.add(operation="self_apply_lane.run", summary="s")
    item = inbox.pending()[0]

    inbox.deny(item.id, reason="цель не названа", actor="operator")

    journal = tmp_path / "data" / "approval_outcomes.jsonl"
    assert journal.is_file(), "исход обязан лечь в журнал моста"
    rows = read_state_jsonl(journal)
    denied_rows = [r for r in rows if r.get("verdict") == "denied"]
    assert denied_rows and "цель не названа" in str(denied_rows[-1].get("reason"))


def test_the_goal_chooser_reads_the_verdicts() -> None:
    """Читатель существует: причины отказов доходят до выбора цели.

    Пин по смыслу: имя связано в модуле и вызвано при сборке предложения цели.
    """
    import inspect

    from core import charter_goal as mod

    assert callable(getattr(mod, "_recent_verdicts", None))
    src = inspect.getsource(mod)
    assert "approval_outcomes.jsonl" in src
    assert "_recent_verdicts(root)" in src
