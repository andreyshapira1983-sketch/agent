"""«Да» оператора должно доходить до того, кто его спрашивал.

Background: docs/CODE_NOTES.md, "The approval nobody read".
"""
from __future__ import annotations

from pathlib import Path

from core.approval_inbox import ApprovalInbox
from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig

_GOAL = "найди и почини свои дефекты"


def _inbox(tmp_path: Path) -> ApprovalInbox:
    return ApprovalInbox(path=tmp_path / "approval_inbox.jsonl")


def _ask(inbox: ApprovalInbox, goal: str):
    """Заявка ровно той формы, что заводит затвор."""
    return inbox.add(
        operation="autonomous_runtime.allow_effects",
        summary="effects disabled",
        risk="irreversible",
        reasons=("non-dry-run autonomous mode is not enabled in this MVP",),
        payload={"dedup_key": AutonomousRuntime._effects_dedup_key(goal)},
    )


def _runtime(inbox: ApprovalInbox) -> AutonomousRuntime:
    runtime = AutonomousRuntime.__new__(AutonomousRuntime)
    runtime.approval_inbox = inbox
    return runtime


def test_an_approved_request_is_found_for_the_same_goal(tmp_path: Path):
    """Живой круг 2026-08-15: оператор одобрил `ain_369d8fdb`, следующий прогон
    встал на `ain_bea806cc` — с ТЕМ ЖЕ ключом. Заявку заводили, ответ не читали.
    """
    inbox = _inbox(tmp_path)
    item = _ask(inbox, _GOAL)
    inbox.approve(item.id)

    found = _runtime(inbox)._granted_effects_approval(
        AutonomousRuntimeConfig(goal=_GOAL, dry_run=False)
    )

    assert found is not None
    assert found.id == item.id


def test_an_approval_for_another_goal_does_not_unlock_this_one(tmp_path: Path):
    """Одобряют цель, а не режим. Иначе одно «да» открыло бы любую работу."""
    inbox = _inbox(tmp_path)
    other = _ask(inbox, "совсем другая цель")
    inbox.approve(other.id)

    assert _runtime(inbox)._granted_effects_approval(
        AutonomousRuntimeConfig(goal=_GOAL, dry_run=False)
    ) is None


def test_a_pending_request_is_not_a_yes(tmp_path: Path):
    """Заявка, которую ещё не решили, разрешением не является."""
    inbox = _inbox(tmp_path)
    _ask(inbox, _GOAL)

    assert _runtime(inbox)._granted_effects_approval(
        AutonomousRuntimeConfig(goal=_GOAL, dry_run=False)
    ) is None


def test_a_denied_request_is_not_a_yes(tmp_path: Path):
    """Улов не отдан: отказ обязан остаться отказом."""
    inbox = _inbox(tmp_path)
    item = _ask(inbox, _GOAL)
    inbox.deny(item.id, reason="нет, эффекты не разрешены")

    assert _runtime(inbox)._granted_effects_approval(
        AutonomousRuntimeConfig(goal=_GOAL, dry_run=False)
    ) is None


def test_a_spent_approval_is_not_reused(tmp_path: Path):
    """Разрешение ОДНОРАЗОВОЕ. Право §9 решать за каждый ход остаётся у
    человека; изменилось лишь то, что его решение теперь читают.
    """
    inbox = _inbox(tmp_path)
    item = _ask(inbox, _GOAL)
    inbox.approve(item.id)
    inbox.mark_executed(item.id)

    assert _runtime(inbox)._granted_effects_approval(
        AutonomousRuntimeConfig(goal=_GOAL, dry_run=False)
    ) is None


def test_the_key_that_asks_is_the_key_that_finds():
    """Один ключ на цель. Разойдись они — заявка снова стала бы письмом в
    никуда, и на этот раз молча.
    """
    key = AutonomousRuntime._effects_dedup_key(_GOAL)

    assert key.startswith("autonomous_runtime.allow_effects:")
    assert key == AutonomousRuntime._effects_dedup_key(_GOAL)
    assert key != AutonomousRuntime._effects_dedup_key(_GOAL + " ещё")
