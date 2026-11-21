"""A fallback in the synthesis phase must say that it happened.

Census item A4. Two handlers in `core/loop_synthesis.py` fell back correctly and
said nothing, and in both cases the fallback is right — what was wrong is the
silence.

**The synthesis contract.** `:227` reads the active contract from the prompt
registry, and the comment directly above says why: so an override "actually
takes effect here instead of being silently ignored". The handler beneath it was
the thing that ignored it. Measured: the built-in `SYSTEM_ANSWER` requires
section headers; a task-specific contract need not. On a registry failure the
operator's contract is replaced by the generic one, `_synthesis_expects_contract_headers`
becomes True instead of False, and the verifier then marks the answer
`malformed_output` for missing headers that contract never asked for. A wrong
verdict about the answer, caused by a swallowed error about the prompt.

**The cheap model tier.** `:572` falls back to the normal model when the LIGHT
tier cannot be selected. Also correct — a tier that will not resolve is no
reason to fail a turn. But `cheap_path_active` stays True, the run continues
under the cheap-path budget, and the only difference in the journal was the
absence of `cheap_path_synth_model` — which reads exactly like a turn that never
took the cheap path at all. The cost saving the path exists for is gone and
nothing says so.

Both now go through `_sensor_failed`, the layer's own answer to this, which
required declaring it in the host contract first — the same root cause as A3:
the cure existed, the connection did not.
"""
from __future__ import annotations

from typing import Any

from core.answer_format import SYSTEM_ANSWER
from core.loop_synthesis import AgentLoopSynthesis, SynthesisState
from core.planner import PlannerOutput


class _Log:
    trace_id = "trace"

    def __init__(self) -> None:
        self.events: list[str] = []

    def log(self, name, payload=None, **kw):
        self.events.append(name)


class _Agent(AgentLoopSynthesis):
    def __init__(self) -> None:
        self.log = _Log()
        self.llm = None
        self.last_provenance = None
        self.last_role_context = None
        self.last_user_profile = None
        self.last_referent_decision = None
        self.model_router = None
        self._cycle_findings: list[dict[str, Any]] = []
        self._stream_on_token = None
        self._last_synth_degraded = False
        self.sensor_failures: list[str] = []

    def _sensor_failed(self, sensor: str, exc: BaseException) -> None:
        self.sensor_failures.append(sensor)

    def memory_record_lines(self, records):
        return []

    def _save_budget_pause_checkpoint(self, *a, **kw):
        raise AssertionError("not reached in these tests")


# ---------------------------------------------------------------------------
# The synthesis contract
# ---------------------------------------------------------------------------


def test_a_registry_failure_is_reported(monkeypatch):
    """Calls the REAL method — no copy of the branch lives in this file.

    It was a copy at first, guarded against drift by grepping the source. The
    function-length ratchet then pushed the branch out into
    `_resolve_synthesis_contract`, which is small enough to call directly. A
    test that exercises the shipped code needs no guard against describing code
    that no longer exists.
    """
    agent = _Agent()
    monkeypatch.setattr(
        "core.prompt_registry.get_prompt",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("registry down")),
    )

    prompt = agent._resolve_synthesis_contract()

    assert prompt == SYSTEM_ANSWER, "the fallback itself must stay"
    assert agent.sensor_failures == ["synthesis_contract_registry"]


# ---------------------------------------------------------------------------
# The cheap model tier
# ---------------------------------------------------------------------------

def _state(**kw) -> SynthesisState:
    base: dict[str, Any] = {
        "goal": None, "user_question": "q", "file_hint": None, "artifacts": {},
        "planner_out": PlannerOutput(reasoning="", sources=[], raw_response=""),
        "plan": None, "history": "", "persistent_block": "", "spend_block": "",
        "failure_history": [], "replan_exhausted": False,
        "cheap_path_active": True, "local_critique_active": False,
        "_task_synth_llm": "NORMAL-MODEL", "_cp": None,
    }
    base.update(kw)
    return SynthesisState(**base)


class _Router:
    def __init__(self, *, explode: bool) -> None:
        self.explode = explode
        self.tiers: list[Any] = []

    def for_task(self, *a, force_tier=None, **kw):
        self.tiers.append(force_tier)
        if self.explode:
            raise RuntimeError("no LIGHT tier available")
        return "CHEAP-MODEL"


class _NoTools:
    def get(self, name):
        raise KeyError(name)


def _synthesizer_model(agent: _Agent, st: SynthesisState) -> Any:
    """Run the real `_run_synthesizer_ladder`; return the model handed to the synthesiser.

    Only `_synthesize` is stubbed, so the tier branch under test is the shipped one.
    """
    handed: list[Any] = []

    def synthesize(**kw):
        handed.append(kw["llm"])
        return "answer"

    agent._synthesize = synthesize
    agent.registry = _NoTools()
    agent._run_synthesizer_ladder(st)
    assert st.draft_answer == "answer", "the ladder degraded: the stub setup is incomplete"
    return handed[0]


def test_a_failed_cheap_tier_is_reported_and_still_answers():
    """Reported, and the turn still runs — the fallback was never the problem.

    Before this, the only trace was the ABSENCE of `cheap_path_synth_model`,
    which reads identically to a turn that never took the cheap path.
    """
    agent = _Agent()
    agent.model_router = _Router(explode=True)

    llm = _synthesizer_model(agent, _state())

    assert llm == "NORMAL-MODEL", "falling back to the normal model must stay"
    assert "cheap_path_synth_model" not in agent.log.events
    assert agent.sensor_failures == ["cheap_path_model_tier"]
