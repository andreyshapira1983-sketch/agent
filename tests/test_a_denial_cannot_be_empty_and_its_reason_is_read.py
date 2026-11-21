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
