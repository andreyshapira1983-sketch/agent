"""Подтверждённый диагноз обязан доехать до заявки на ремонт.

Background: docs/CODE_NOTES.md, "A diagnosis that dies in a digest".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.best_next_action import BestNextAction
from core.campaign_io import _propose_repair_from_diagnosis
from core.campaign_types import CampaignConfig

#: Диагноз четвёртого прогона 2026-08-15 — 6 из 6 подтверждённых — называл
#: настоящие файлы. Здесь та же форма.
_ANSWER = (
    "В аудите зафиксировано событие reasoning_action_mismatch; место проверки — "
    "core/reasoning_action_check.py, обвязка в core/loop.py."
)


@dataclass
class _Verification:
    total_chunks: int = 6
    verified_chunks: int = 6


@dataclass
class _Proposal:
    path: str = "core/reasoning_action_check.py"
    proposed_content: str = "# fixed module body\n"
    reason: str = "align check with planned tools"
    test_paths: tuple = ("tests",)
    test_pattern: str | None = None


@dataclass
class _GenReport:
    ok: bool = True
    status: str = "proposed"
    proposal: _Proposal | None = field(default_factory=_Proposal)
    diagnosis: str = "mismatch between plan and prose"
    confidence: float = 0.8
    evidence: tuple = ("log:trace_x:1",)


class _Inbox:
    def __init__(self) -> None:
        self.items: list = []

    def add(self, **kw):
        self.items.append(kw)

        class _Item:
            id = "ain_test"

        return _Item()


class _Agent:
    def __init__(self, gen=None, verification=None) -> None:
        self.last_verification = verification or _Verification()
        self._gen = gen or _GenReport()
        self.proposed_with: dict | None = None
        self.log = None

    def propose_repair(self, **kw):
        self.proposed_with = kw
        return self._gen


def _action(name: str = "improve_failure_to_idea_pipeline") -> BestNextAction:
    return BestNextAction(action=name, title="t", severity="medium",
                          priority=55, reason="r")


def _run(agent, *, action=None, config=None, inbox=None, answer=_ANSWER):
    return _propose_repair_from_diagnosis(
        agent=agent, workspace=".", config=config or CampaignConfig(
            goal="найди и почини", dry_run=False),
        action=action or _action(), answer=answer,
        approval_inbox=_Inbox() if inbox is None else inbox,
    )


def test_a_verified_diagnosis_lands_in_the_approval_queue():
    """Четвёртый прогон дня дошёл до 6/6 — и умер в дайджесте. Теперь он
    обязан стать заявкой, которую решает человек.
    """
    inbox = _Inbox()
    agent = _Agent()

    note = _run(agent, inbox=inbox)

    assert note == "repair_proposed:ain_test"
    assert len(inbox.items) == 1
    item = inbox.items[0]
    assert item["operation"] == "self_apply_lane.run"
    assert item["payload"]["files"][0]["path"] == "core/reasoning_action_check.py"
    assert agent.proposed_with["target_path"] == "core/reasoning_action_check.py"


def test_a_partially_verified_diagnosis_is_not_trusted_with_a_patch():
    """Тот же стандарт, что снимает прокси релевантности: частично
    обоснованный текст патча не заслуживает.
    """
    inbox = _Inbox()
    agent = _Agent(verification=_Verification(total_chunks=6, verified_chunks=4))

    assert _run(agent, inbox=inbox) is None
    assert inbox.items == []
    assert agent.proposed_with is None


def test_dry_run_creates_no_durable_item():
    """Заявка в очереди — долговременный эффект; сухой прогон их не делает."""
    inbox = _Inbox()

    note = _run(_Agent(), inbox=inbox,
                config=CampaignConfig(goal="g", dry_run=True))

    assert note is None and inbox.items == []


def test_other_actions_do_not_manufacture_repairs():
    """Health-pass ремонта не обещал: провод живёт только на действии про
    собственный дефект.
    """
    inbox = _Inbox()

    assert _run(_Agent(), inbox=inbox, action=_action("observe")) is None
    assert inbox.items == []


def test_a_diagnosis_naming_no_real_file_proposes_nothing():
    """Адрес спрашивается у диска. Выдуманные reasoning.py и citation.py уже
    один раз завели реестр в пустоту.
    """
    inbox = _Inbox()

    note = _run(_Agent(), inbox=inbox,
                answer="дефект в core/imaginary_module.py и вообще")

    assert note is None and inbox.items == []


def test_a_declined_generation_is_reported_not_hidden():
    """Отказ генератора — честный итог, а не молчание: оператор видит, что
    переход состоялся и почему остановился.
    """
    agent = _Agent(gen=_GenReport(ok=False, status="rejected", proposal=None))

    assert _run(agent) == "repair_declined:rejected"


def test_nothing_is_executed_here():
    """Провод только предлагает: ни записи файлов, ни запуска ленты — решение
    остаётся за человеком (§9).
    """
    inbox = _Inbox()
    _run(_Agent(), inbox=inbox)

    item = inbox.items[0]
    assert item["payload"]["origin"] == "campaign_diagnosis"
    assert "content" in item["payload"]["files"][0]  # патч ждёт решения, не применён
