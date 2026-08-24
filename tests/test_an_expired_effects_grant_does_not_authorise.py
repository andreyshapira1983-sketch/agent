"""Истёкшее разрешение на эффекты больше не разрешает.

СВЕРКА С ПОЛЕМ, класс H-41 (docs/audit/HISTORICAL_FAILURE_LEDGER.md) —
«усечённый хеш в решении о доверии», семейство коллизий MD5. Сам коллизионный
вопрос закрыт арифметикой: слабых алгоритмов нет ни одного, а при живых объёмах
(137 заявок, 142 эпизода) вероятность столкновения 48-битного отпечатка ≈ 3e-11,
64-битного ≈ 6e-16. Хеш здесь ни при чём.

Проба вскрыла другое — то, к чему хеш ведёт. Разрешение на НЕОБРАТИМЫЕ эффекты
находится по ключу `autonomous_runtime.allow_effects:<16 знаков от цели>` среди
`list(status="approved")`, и этот читатель не отсекает истёкшие: заявка со
сроком, прошедшим тридцать дней назад, невидима для `pending()` и полностью
видима здесь.

ПОЧЕМУ ЭТО НЕ ВЫДУМАННАЯ ПОЛИТИКА. Сосед по файлу — `_active_standing_grant` —
уже читает `expires_at` у ОДОБРЕННОЙ заявки как срок действия гранта и
пропускает истёкшие, а сверх того держит суточный потолок с учётом
израсходованного. Два механизма разрешения на одно и то же, и они расходятся
ровно на защите. Здесь второй приводится к первому в той половине, где смысл
поля уже установлен этим же кодом.

ЧЕГО ЗДЕСЬ НЕ ДЕЛАЕТСЯ. Потолок не вводится: сколько раз одно разрешение может
сработать — это решение оператора о необратимом, а не экспертный дефолт.
Приводится только срок.
"""
from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import pytest

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


def test_an_expired_grant_no_longer_authorises(tmp_path) -> None:
    inbox = ApprovalInbox(path=tmp_path / "inbox.jsonl")
    config = _Config()
    past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    _grant(inbox, goal=config.goal, expires_at=past)

    found = _runtime(tmp_path, inbox)._granted_effects_approval(config)

    assert found is None, (
        "разрешение на необратимые эффекты, срок которого истёк тридцать дней "
        "назад, всё ещё разрешает их"
    )


def test_a_live_grant_still_authorises(tmp_path) -> None:
    """Контроль: без него первый тест проходил бы и на сломанном поиске."""
    inbox = ApprovalInbox(path=tmp_path / "inbox.jsonl")
    config = _Config()
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    granted = _grant(inbox, goal=config.goal, expires_at=future)

    found = _runtime(tmp_path, inbox)._granted_effects_approval(config)

    assert found is not None and found.id == granted.id


def test_a_grant_for_another_goal_never_authorises(tmp_path) -> None:
    """Граница, которая была верна и до правки: цель A не разрешает цель B."""
    inbox = ApprovalInbox(path=tmp_path / "inbox.jsonl")
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    _grant(inbox, goal="совсем другая цель", expires_at=future)

    assert _runtime(tmp_path, inbox)._granted_effects_approval(_Config()) is None


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
    import inspect

    from core.autonomous_runtime import AutonomousRuntime

    src = inspect.getsource(AutonomousRuntime._active_standing_grant)
    assert "expires" in src and "continue" in src, (
        "стоячий грант больше не проверяет срок — основание для правки "
        "разрешения на эффекты исчезло, перечитайте H-41"
    )


@pytest.mark.parametrize("goal", ["цель один", "цель два"])
def test_the_key_is_still_scoped_by_goal(goal: str) -> None:
    """Граница: ключ остаётся привязкой к цели, а не к чему-то шире."""
    from core.autonomous_runtime import AutonomousRuntime

    key = AutonomousRuntime._effects_dedup_key(goal)
    assert key.startswith("autonomous_runtime.allow_effects:")
    assert key != AutonomousRuntime._effects_dedup_key(goal + " ещё")
