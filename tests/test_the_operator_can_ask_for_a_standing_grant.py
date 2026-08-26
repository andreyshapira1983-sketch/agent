"""Стоячий грант должен заводиться боевым путём, а не только в тестах.

Замер, отвергнутые варианты и границы: MIR-166 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from pathlib import Path

from cli.command_dispatch import handle_meta_command
from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH, ApprovalInbox

_OPERATION = "autonomous_runtime.standing_grant"


class _Log:
    def log(self, *args, **kwargs) -> None:
        return None


class _Agent:
    """Ровно то, что нужно обработчику: инбокс и журнал."""

    def __init__(self, workspace: Path) -> None:
        self.approval_inbox = ApprovalInbox(
            path=workspace / DEFAULT_APPROVAL_INBOX_PATH
        )
        self.log = _Log()


def _run(command: str, workspace: Path) -> tuple[bool, _Agent]:
    agent = _Agent(workspace)
    handled = handle_meta_command(command, agent, workspace)
    return handled, agent


def test_a_shipped_command_can_file_the_request(tmp_path: Path) -> None:
    """Красный свидетель: автомат ЧИТАЕТ стоячий грант, а завести его нечем.

    `AutonomousRuntime._active_standing_grant` ищет заявку с этой операцией, но
    во всём дереве её заводил только тестовый файл. Механизм безнадзорной
    работы существовал в коде и был недостижим ни одним действием оператора —
    читатель без писателя, зеркало MIR-138.
    """
    handled, agent = _run(":standing-grant 6 48", tmp_path)

    assert handled
    filed = [i for i in agent.approval_inbox.list() if i.operation == _OPERATION]
    assert filed, "боевой команды, заводящей стоячий грант, по-прежнему нет"
    assert (filed[0].payload or {}).get("max_runs_per_day") == 6


def test_filing_is_not_granting(tmp_path: Path) -> None:
    """Просьба и разрешение — разные события и не сливаются в одно.

    Норма ратифицирована оператором 2026-08-22 (MIR-117, правило B). Поэтому
    команда КЛАДЁТ заявку, а открывает её отдельное слово через
    `:approval-approve` — иначе оператор одобрял бы сам себе одной строкой.
    """
    _, agent = _run(":standing-grant 6 48", tmp_path)

    filed = [i for i in agent.approval_inbox.list() if i.operation == _OPERATION]
    assert filed[0].status == "pending"


def test_the_grant_carries_its_risk_and_its_end(tmp_path: Path) -> None:
    """Бессрочный грант с необъявленным риском — не грант, а дыра."""
    _, agent = _run(":standing-grant 6 48", tmp_path)

    item = next(i for i in agent.approval_inbox.list() if i.operation == _OPERATION)
    assert item.risk == "irreversible"
    assert item.expires_at


def test_a_grant_without_numbers_is_refused(tmp_path: Path) -> None:
    """Границы обязательны: без числа прогонов автомат читает 0 и грант мёртв."""
    for bad in (":standing-grant", ":standing-grant 0 48", ":standing-grant 6 0"):
        _, agent = _run(bad, tmp_path)
        filed = [i for i in agent.approval_inbox.list() if i.operation == _OPERATION]
        assert not filed, bad
