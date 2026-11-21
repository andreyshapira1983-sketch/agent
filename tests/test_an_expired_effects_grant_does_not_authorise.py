"""Истёкшее разрешение на эффекты больше не разрешает.

Замер, отвергнутые варианты и границы: H-41 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

from core.approval_inbox import ApprovalInbox

_KEY = "autonomous_runtime.allow_effects:deadbeefdeadbeef"


class _Config:
    goal = "цель, ради которой дано разрешение"


def _runtime(tmp_path: pathlib.Path, inbox: ApprovalInbox):
    """Голый экземпляр: нужен только метод поиска разрешения."""
    from core.autonomous_runtime import AutonomousRuntime

    runtime = AutonomousRuntime.__new__(AutonomousRuntime)
    runtime.approval_inbox = inbox
    runtime.workspace = tmp_path
    return runtime


def _grant(inbox: ApprovalInbox, *, goal: str, expires_at: str | None):
    from core.autonomous_runtime import AutonomousRuntime

    item = inbox.add(
        operation="autonomous_runtime.allow_effects",
        summary="эффекты",
        risk="irreversible",
        reasons=("оператор одобрил",),
        payload={"dedup_key": AutonomousRuntime._effects_dedup_key(goal)},
        expires_at=expires_at,
    )
    inbox.approve(item.id)
    return item


def test_a_live_grant_still_authorises(tmp_path) -> None:
    """Контроль: без него первый тест проходил бы и на сломанном поиске."""
    inbox = ApprovalInbox(path=tmp_path / "inbox.jsonl")
    config = _Config()
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    granted = _grant(inbox, goal=config.goal, expires_at=future)

    found = _runtime(tmp_path, inbox)._granted_effects_approval(config)

    assert found is not None and found.id == granted.id


def test_a_malformed_deadline_is_refused_not_trusted(tmp_path) -> None:
    """Нечитаемый срок — это неизвестность, а не разрешение.

    Направление отказа выбрано по цене: пропустить необратимое действие по
    непрочитанной отметке хуже, чем потребовать нового одобрения.
    """
    inbox = ApprovalInbox(path=tmp_path / "inbox.jsonl")
    config = _Config()
    _grant(inbox, goal=config.goal, expires_at="не-дата")

    assert _runtime(tmp_path, inbox)._granted_effects_approval(config) is None


def test_the_standing_grant_already_did_this(tmp_path) -> None:
    """Замер, на котором стоит вся запись: сосед уже читает срок так же.

    Если это когда-нибудь перестанет быть правдой, приведение одного механизма
    к другому теряет основание, и тест обязан этого потребовать.
    """
    from core.autonomous_runtime import active_standing_grant

    # Блок 5 (аудит G3, 2026-09-03): прежде здесь стояла подстрока
    # `"expires" in src and "continue" in src` — её выдержал бы и код,
    # который срок читает и игнорирует. Свойство проверяется ПОВЕДЕНИЕМ:
    # истёкший стоячий грант не находится, живой — находится.
    def _standing(inbox: ApprovalInbox, *, expires_at: str):
        item = inbox.add(
            operation="autonomous_runtime.standing_grant", summary="стоячий",
            risk="irreversible", reasons=("оператор",),
            payload={"max_runs_per_day": 3}, expires_at=expires_at,
        )
        inbox.approve(item.id)
        return item

    expired = ApprovalInbox(path=tmp_path / "expired.jsonl")
    _standing(expired, expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    assert active_standing_grant(expired, tmp_path) is None, (
        "стоячий грант больше не проверяет срок — основание для правки "
        "разрешения на эффекты исчезло, перечитайте H-41"
    )

    live = ApprovalInbox(path=tmp_path / "live.jsonl")
    fresh = _standing(live, expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    assert active_standing_grant(live, tmp_path).id == fresh.id
