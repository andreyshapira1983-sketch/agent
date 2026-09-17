"""Стоячий грант расходуется КАЖДЫМ, кто им пользуется.

WHY THIS EXISTS. Аудит автономности 2026-09-17, находка 7: в репозитории два
потребителя одного и того же стоячего гранта.

* `AutonomousRuntime.run` спрашивает грант, а потом ЗАПИСЫВАЕТ расход
  (`_record_standing_use`) — дневной лимит для него настоящий;
* `drain_rule_approved_proposals` спрашивал тот же грант и не записывал ничего,
  а дальше сам же ставил «одобрено» от имени правила и применял изменение.

Следствие проверено на живом коде: `max_runs_per_day=1` не мешал проходу
применить сколько угодно предложений за один тик, а расход, посчитанный вторым
потребителем, был не виден первому. «Одно да в неделю» превращалось в
безлимитное да, и стены гранта существовали только на одном из двух путей.

Здесь проверяется ровно это: у каждого применённого эффекта есть событие
разрешения В ЖУРНАЛЕ РАСХОДА, и потолок считает обоих потребителей.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH, ApprovalInbox
from core.autonomous_runtime import (
    _record_standing_use,
    active_standing_grant,
    standing_runs_today,
)
from core.self_apply_bridge import SELF_APPLY_OPERATION, build_self_apply_payload


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path


def _inbox(workspace: Path) -> ApprovalInbox:
    return ApprovalInbox(path=workspace / DEFAULT_APPROVAL_INBOX_PATH)


def _grant(inbox: ApprovalInbox, *, runs_per_day: int = 5, days: int = 7):
    item = inbox.add(
        operation="autonomous_runtime.standing_grant",
        summary=f"standing effects grant: {runs_per_day} runs/day",
        risk="irreversible",
        payload={"max_runs_per_day": runs_per_day},
        expires_at=(datetime.now(timezone.utc) + timedelta(days=days)).isoformat(),
    )
    return inbox.approve(item.id)


def _pending_document(inbox: ApprovalInbox, workspace: Path, name: str):
    """Заявка ровно того класса, который правило разрешает: НОВЫЙ документ."""
    payload = build_self_apply_payload(
        files=[{"path": f"knowledge/doctrine/future/{name}.md", "content": "# заметка\n"}],
        reason="черновик",
        origin="test",
        workspace=workspace,
    )
    return inbox.add(
        operation=SELF_APPLY_OPERATION,
        summary=f"self-apply: {name}.md",
        risk="reversible",
        payload=payload,
    )


class _Applications:
    """Подделка полосы: считает применения, ничего не пишет на диск."""

    def __init__(self) -> None:
        self.item_ids: list[str] = []

    def __call__(self, **kwargs: Any) -> dict:
        self.item_ids.append(kwargs["item_id"])
        return {"status": "committed_local", "proposal_id": kwargs["item_id"]}


@pytest.fixture()
def lane(monkeypatch: Any) -> _Applications:
    import core.self_apply_bridge as bridge

    fake = _Applications()
    monkeypatch.setattr(bridge, "run_approved_self_apply", fake)
    return fake


def test_every_applied_effect_has_an_approval_event(
    workspace: Path, lane: _Applications
) -> None:
    """Красный свидетель: два применения — ноль записей о расходе гранта.

    Полномочие на применение бралось из гранта, а след расхода оставался
    пустым, то есть по журналу нельзя было ответить, чем именно разрешено
    каждое изменение.
    """
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    grant = _grant(inbox, runs_per_day=5)
    _pending_document(inbox, workspace, "first")
    _pending_document(inbox, workspace, "second")

    out = drain_rule_approved_proposals(workspace, dry_run=False)

    assert out["applied"] == 2, out
    assert len(lane.item_ids) == 2
    assert standing_runs_today(workspace, grant.id) == 2, (
        "применения без записи расхода: грант выдал полномочие бесследно"
    )


def test_standing_grant_cap_counts_all_consumers(
    workspace: Path, lane: _Applications
) -> None:
    """Потолок общий: расход рантайма уменьшает остаток правила, и наоборот.

    Иначе «3 прогона в день» означает 3 прогона рантайма ПЛЮС неограниченное
    число применений правилом — то есть не означает ничего.
    """
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    grant = _grant(inbox, runs_per_day=2)
    # Первый потребитель уже израсходовал одно разрешение из двух.
    _record_standing_use(workspace, grant.id)

    _pending_document(inbox, workspace, "alpha")
    _pending_document(inbox, workspace, "beta")
    _pending_document(inbox, workspace, "gamma")

    out = drain_rule_approved_proposals(workspace, dry_run=False)

    assert out["applied"] == 1, (
        f"остаток гранта — одно разрешение, применено {out['applied']}"
    )
    assert standing_runs_today(workspace, grant.id) == 2
    assert active_standing_grant(inbox, workspace) is None, (
        "исчерпанный грант обязан перестать быть действующим для ОБОИХ путей"
    )


def test_an_exhausted_grant_applies_nothing(
    workspace: Path, lane: _Applications
) -> None:
    """Исчерпанный грант — это отказ по названной причине, а не тихий пропуск."""
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    grant = _grant(inbox, runs_per_day=1)
    _record_standing_use(workspace, grant.id)
    _pending_document(inbox, workspace, "alpha")

    out = drain_rule_approved_proposals(workspace, dry_run=False)

    assert out["applied"] == 0
    assert out["blocked"] == "no active standing grant"
    assert lane.item_ids == []


def test_an_expired_grant_applies_nothing(
    workspace: Path, lane: _Applications
) -> None:
    """Срок — тоже стена: вчерашнее «да» сегодня не полномочие."""
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _grant(inbox, runs_per_day=5, days=-1)
    _pending_document(inbox, workspace, "alpha")

    out = drain_rule_approved_proposals(workspace, dry_run=False)

    assert out["applied"] == 0
    assert out["blocked"] == "no active standing grant"
    assert lane.item_ids == []


def test_the_drain_still_applies_what_the_rule_allows(
    workspace: Path, lane: _Applications
) -> None:
    """Учёт не отменяет саму петлю: разрешённый документ по-прежнему проходит."""
    from core.rule_approved_apply import drain_rule_approved_proposals

    inbox = _inbox(workspace)
    _grant(inbox, runs_per_day=5)
    item = _pending_document(inbox, workspace, "alpha")

    out = drain_rule_approved_proposals(workspace, dry_run=False)

    assert out == {"considered": 1, "applied": 1, "refused": 0, "blocked": ""}
    assert lane.item_ids == [item.id]
    assert inbox.get(item.id).status == "approved"
