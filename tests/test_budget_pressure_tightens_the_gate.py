"""«Тревога» числом: на исходе бюджета обратимые действия ждут человека.

Журнал оператора «Эмоции»: тревога рядом с необратимым — строже ворота, и
эмоции никогда не ослабляют тормоза. Anthropic (arXiv 2604.07729): «отчаяние»
модели включается на исходе бюджета и ведёт к подгонке. Порог — принятое в
коде «окно почти исчерпано» (core/self_build_supervisor).
"""
from __future__ import annotations

from types import SimpleNamespace

from core.models import Action
from core.policy import PolicyGate
from core.pressure_gate import budget_pressure


def _tool(name: str, risk: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, risk_for=lambda _args: risk)


class _Registry:
    def __init__(self) -> None:
        self.tools = {"file_write": _tool("file_write", "reversible"), "file_read": _tool("file_read", "read_only"),
                      "web_post": _tool("web_post", "external")}

    def get(self, name: str):
        return self.tools[name]


def _gate(pressed: str) -> PolicyGate:
    gate = PolicyGate(_Registry())
    gate.pressure = lambda: pressed
    return gate


def _call(gate: PolicyGate, tool: str) -> str:
    return gate.check(Action(step_id="s1", type="tool_call", tool_name=tool, parameters={})).decision


def test_under_pressure_a_reversible_effect_waits_and_reading_goes_on() -> None:
    gate = _gate("hour llm_calls headroom 2/100 <= 3")
    assert _call(gate, "file_write") == "escalate"
    assert _call(gate, "file_read") == "allow"
    assert _call(gate, "web_post") == "escalate", "pressure never loosens anything"


def test_without_pressure_nothing_changes() -> None:
    assert _call(_gate(""), "file_write") == "allow"


def _ledger(used: int, limit: int) -> SimpleNamespace:
    snap = {"windows": [{"name": "hour", "counters": {"llm_calls": {"used": used, "limit": limit}}}]}
    return SimpleNamespace(snapshot=lambda: snap)


def test_pressure_is_the_existing_near_exhaustion_rule() -> None:
    assert budget_pressure(_ledger(98, 100)) != ""
    assert budget_pressure(_ledger(50, 100)) == ""
    assert budget_pressure(SimpleNamespace(snapshot=lambda: 1 / 0)) == "", "an unreadable ledger invents nothing"


def test_the_agent_is_built_with_the_gate(tmp_path) -> None:
    from app.bootstrap import build_agent

    agent = build_agent(tmp_path, with_memory=False)
    assert callable(agent.policy.pressure) and agent.policy.pressure() == ""
