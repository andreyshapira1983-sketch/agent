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

import ast
import inspect
from typing import Any

import core.loop  # noqa: F401 — populates the prompt registry, see below
from core.answer_format import SYSTEM_ANSWER, output_contract_requires_headers
from core.loop_synthesis import AgentLoopSynthesis, SynthesisState


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
# The mixin must be able to reach the cure
# ---------------------------------------------------------------------------

def test_the_file_declares_the_sensor_it_needs():
    """The root cause, pinned apart from the symptom — as in A3.

    Both handlers were silent because `_sensor_failed` was not in this file's
    host contract. Declaring it is the fix; this checks the declaration rather
    than trusting that the calls below happen to resolve.
    """
    import core.loop_synthesis as mod

    tree = ast.parse(inspect.getsource(mod))
    declared: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            declared |= {
                sub.target.id
                for sub in ast.walk(node)
                if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name)
            }
    assert "_sensor_failed" in declared


# ---------------------------------------------------------------------------
# The synthesis contract
# ---------------------------------------------------------------------------

def test_the_registry_really_is_the_source_of_the_contract():
    """Baseline. Without it the failure case below proves nothing.

    `core.loop` registers `synthesizer.system` on import, which is why this
    module imports it. A first measurement of this defect ran without that
    import, found an empty registry, and briefly suggested the fallback fires
    on every single turn — it does not, and the difference matters for how
    serious the finding is.
    """
    from core.prompt_registry import get_prompt

    assert get_prompt("synthesizer.system") == SYSTEM_ANSWER


def test_a_task_specific_contract_and_the_generic_one_disagree_about_headers():
    """The mechanism by which a swallowed prompt error becomes a wrong verdict.

    Not a hypothetical: `output_contract_requires_headers` is what sets
    `_synthesis_expects_contract_headers`, and the verifier reads that to decide
    whether a missing section header means `malformed_output`.
    """
    table_only = "Answer as a single markdown table. No section headers."

    assert output_contract_requires_headers(SYSTEM_ANSWER) is True
    assert output_contract_requires_headers(table_only) is False


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


def test_a_healthy_registry_reports_nothing():
    """Silence must remain available for the case that is actually fine."""
    agent = _Agent()

    prompt = agent._resolve_synthesis_contract()

    assert prompt == SYSTEM_ANSWER
    assert agent.sensor_failures == []


# ---------------------------------------------------------------------------
# The cheap model tier
# ---------------------------------------------------------------------------

def _state(**kw) -> SynthesisState:
    base: dict[str, Any] = {
        "goal": None, "user_question": "q", "file_hint": None, "artifacts": {},
        "planner_out": None, "plan": None, "history": "", "persistent_block": "",
        "failure_history": [], "replan_exhausted": False,
        "cheap_path_active": True, "local_critique_active": False,
        "_task_synth_llm": "NORMAL-MODEL", "_cp": None,
    }
    base.update(kw)
    return SynthesisState(**base)


class _Router:
    def __init__(self, *, explode: bool) -> None:
        self.explode = explode

    def for_task(self, *a, **kw):
        if self.explode:
            raise RuntimeError("no LIGHT tier available")
        return "CHEAP-MODEL"


def _select_tier(agent: _Agent, st: SynthesisState):
    """The tier branch alone — the ladder below it needs a live synthesiser."""
    from core.model_router import ModelRole
    from core.task_complexity import ComplexityTier

    llm = st._task_synth_llm
    if st.cheap_path_active:
        try:
            llm = agent.model_router.for_task(
                ModelRole.SYNTHESIZER, st.user_question,
                force_tier=ComplexityTier.LIGHT,
            )
            agent.log.log("cheap_path_synth_model")
        except Exception as exc:  # noqa: BLE001 - mirrors the handler under test
            llm = st._task_synth_llm
            agent._sensor_failed("cheap_path_model_tier", exc)
    return llm


def test_a_healthy_cheap_tier_logs_the_model_it_picked():
    agent = _Agent()
    agent.model_router = _Router(explode=False)

    llm = _select_tier(agent, _state())

    assert llm == "CHEAP-MODEL"
    assert "cheap_path_synth_model" in agent.log.events
    assert agent.sensor_failures == []


def test_a_failed_cheap_tier_is_reported_and_still_answers():
    """Reported, and the turn still runs — the fallback was never the problem.

    Before this, the only trace was the ABSENCE of `cheap_path_synth_model`,
    which reads identically to a turn that never took the cheap path.
    """
    agent = _Agent()
    agent.model_router = _Router(explode=True)

    llm = _select_tier(agent, _state())

    assert llm == "NORMAL-MODEL", "falling back to the normal model must stay"
    assert "cheap_path_synth_model" not in agent.log.events
    assert agent.sensor_failures == ["cheap_path_model_tier"]


def test_a_turn_that_never_took_the_cheap_path_reports_nothing():
    """The pair the old code could not tell apart, now distinguishable."""
    agent = _Agent()
    agent.model_router = _Router(explode=True)

    llm = _select_tier(agent, _state(cheap_path_active=False))

    assert llm == "NORMAL-MODEL"
    assert "cheap_path_synth_model" not in agent.log.events
    assert agent.sensor_failures == []


# ---------------------------------------------------------------------------
# Both handlers, as they stand in the source
# ---------------------------------------------------------------------------

def test_the_cheap_tier_handler_still_calls_its_sensor():
    """The one branch this file still exercises as a copy.

    The tier selection sits inside `_run_synthesizer_ladder`, which ends in a
    real synthesis call, so it cannot be run here whole. Checking the name in
    the source is the weaker guard — it judges text rather than behaviour — and
    it is kept only where behaviour is genuinely out of reach. The registry
    branch above no longer needs it.
    """
    import core.loop_synthesis as mod

    assert '_sensor_failed("cheap_path_model_tier"' in inspect.getsource(mod)
