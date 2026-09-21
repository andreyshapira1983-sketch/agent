"""Грант закрывает просьбы о разрешении, на которые он и есть ответ.

Живой случай 2026-09-20/21. В 19:00–19:10 у прогона кончился стоячий грант, и
он подал шесть просьб `autonomous_runtime.allow_effects` — «хочу применять
эффекты, разрешения нет». В 19:14 оператор грант продлил, и следующие прогоны
пошли с ним (37 использований после перезапуска). А шесть просьб так и
остались висеть: грант давал разрешение, но не гасил просьб о нём.

Полоса самоправки останавливается при любой висящей заявке — это правило
закреплено тестом (`test_approvals_pending_excludes_current_item` кладёт
именно `allow_effects` как «постороннюю» и требует её счёта), и его здесь не
трогают. Поэтому шесть давно отвеченных просьб держали самоправку закрытой
сутки: 169 циклов 21.09 по одному файлу, ноль работы.

Просьба закрывается статусом `aborted`, а не `approved`: одобренную просьбу
следующий прогон с той же целью подобрал бы как ОДНОРАЗОВОЕ разрешение
(`_granted_effects_approval`) — это была бы утечка полномочия, а не уборка.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.approval_inbox import ApprovalInbox
from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig
from core.self_apply_bridge import SELF_APPLY_OPERATION
from tests.test_autonomous_runtime import _agent


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "README.md").write_text("Project overview.", encoding="utf-8")
    return tmp_path


def _grant(inbox: ApprovalInbox):
    item = inbox.add(
        operation="autonomous_runtime.standing_grant",
        summary="standing effects grant: 3 runs/day", risk="irreversible",
        payload={"max_runs_per_day": 3},
        expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    )
    return inbox.approve(item.id)


def _stale_requests(inbox: ApprovalInbox, n: int = 6) -> list[str]:
    return [
        inbox.add(operation="autonomous_runtime.allow_effects",
                  summary="This run wants to apply effects and holds no permission",
                  risk="irreversible", payload={"goal": f"мёртвая цель {i}"}).id
        for i in range(n)
    ]


def _run(workspace: Path, inbox: ApprovalInbox):
    agent = _agent(workspace, with_tests=False)
    return AutonomousRuntime(agent, workspace=workspace, approval_inbox=inbox).run(
        AutonomousRuntimeConfig(goal="учебная цель автомата", dry_run=False,
                                limit=2, include_tests=False))


def test_the_requests_the_grant_answers_are_closed(workspace: Path) -> None:
    inbox = ApprovalInbox()
    grant = _grant(inbox)
    stale = _stale_requests(inbox)

    _run(workspace, inbox)

    for item_id in stale:
        item = inbox.get(item_id)
        assert item.status == "aborted", item
        assert grant.id in item.decision_reason


def test_they_are_not_turned_into_one_time_permissions(workspace: Path) -> None:
    inbox = ApprovalInbox()
    _grant(inbox)
    stale = _stale_requests(inbox)
    _run(workspace, inbox)
    assert all(inbox.get(i).status != "approved" for i in stale)


def test_a_pending_self_change_request_is_left_alone(workspace: Path) -> None:
    """Грант отвечает на просьбу о разрешении, а не на заявку правки кода:
    её решает человек."""
    inbox = ApprovalInbox()
    _grant(inbox)
    patch = inbox.add(operation=SELF_APPLY_OPERATION, summary="патч", payload={})
    _run(workspace, inbox)
    assert inbox.get(patch.id).status == "pending"


def test_without_a_grant_nothing_is_closed(workspace: Path) -> None:
    inbox = ApprovalInbox()
    stale = _stale_requests(inbox, n=2)
    _run(workspace, inbox)
    assert all(inbox.get(i).status == "pending" for i in stale)
