"""Одобренная, но не исполненная правка кода со сроком в прошлом — снимается (план субботы ж).

24.09: заявка на разрез step_sanitizer (ain_4e7f3c…) висела «одобренной»
бессрочно; когда код снова совпал с её основой, полоса применила её через
полтора часа после того, как оператор её отверг. Постоянные разрешения свой
срок проверяют сами (_grant_is_live) — их это не касается.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from core.approval_inbox import ApprovalInbox
from core.self_apply_bridge import SELF_APPLY_OPERATION


def _item(inbox: ApprovalInbox, operation: str, hours: float):
    exp = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)).isoformat()
    item = inbox.add(operation=operation, summary="t", payload={"files": []}, expires_at=exp)
    inbox.approve(item.id, reason="ok", actor="operator")
    return item.id


def test_an_approved_patch_past_its_deadline_is_aborted(tmp_path: Path) -> None:
    inbox = ApprovalInbox(path=tmp_path / "inbox.jsonl")
    stale = _item(inbox, SELF_APPLY_OPERATION, -1)
    fresh = _item(inbox, SELF_APPLY_OPERATION, +5)
    inbox.expire_stale()
    assert inbox.get(stale).status == "aborted"
    assert inbox.get(fresh).status == "approved"


def test_a_standing_grant_keeps_its_own_clock(tmp_path: Path) -> None:
    inbox = ApprovalInbox(path=tmp_path / "inbox.jsonl")
    grant = _item(inbox, "autonomous_runtime.standing_grant", -1)
    inbox.expire_stale()
    assert inbox.get(grant).status == "approved"
