"""The strict form of the policy-authority proof on the verification path.

`10240f8` proved the two poles (`continue` / `abort_exhausted`) by reaching
them through DIFFERENT scenario paths. The map's protocol item 11 demands the
strict form: two verdicts forced onto IDENTICAL state, so the only varying
input is the verdict itself. This file delivers that, plus the two edges the
map listed as never observed:

- the admission gate in isolation (no unresolved citation -> the policy is
  never consulted; one present -> it is), broken at the GATE, not the detector;
- the `abort_no_retry` pole on the verify path — never seen in a real run
  before; its downstream consequence is IDENTICAL to `abort_exhausted` by
  construction (`if decision.action != "continue"` collapses both), so the
  distinction lives only in the journal's `decision_action`. Asserted as such.

Scenario shared by every test: an empty plan succeeds, synthesis cites a web
URL no evidence can resolve, the verify loop opens. Identical state; only the
verdict (or the wire under mutation) differs.
"""
from __future__ import annotations

from pathlib import Path

from core.model_usage import ModelUsageLimits
from core.replan import ReplanDecision
from tests.conftest import FakeLLM
from tests.test_budget_resume import _build_guarded_agent

CITING_ANSWER = "The fact [web:http://example.com/a] holds."
PLAN_NO_TOOLS = '{"reasoning":"no tools","sources":[]}'


def _agent(workspace: Path, *, answer: str):
    llm = FakeLLM(responses=[PLAN_NO_TOOLS, answer])
    agent = _build_guarded_agent(
        workspace, llm, ModelUsageLimits(),
        verifier_enabled=True, max_replan_attempts=3,
    )
    return agent, llm


def _force_verdict(agent, action: str) -> None:
    agent.replan_policy.decide = lambda failure_history, completed_attempts: (
        ReplanDecision(
            action=action,
            reason="forced by test",
            advice_for_planner="",
            failure_counts={},
            forbidden_actions=(),
        )
    )


# ── protocol item 11: two verdicts forced onto identical state ────────────────

def test_forced_continue_and_forced_abort_take_different_paths(
    workspace: Path,
) -> None:
    """The only varying input is the verdict; the path lengths must differ.

    Obeying `continue` sends the verify loop back to the planner (extra LLM
    calls up to the hard cap); obeying an abort ends the run at exactly the
    two calls the cycle already made. The wire under test is
    `if decision.action != "continue"` (loop_verify_replan).
    """
    agent_a, llm_a = _agent(workspace / "a", answer=CITING_ANSWER)
    _force_verdict(agent_a, "continue")
    agent_a.run(user_question="Explain the repository status")

    agent_b, llm_b = _agent(workspace / "b", answer=CITING_ANSWER)
    _force_verdict(agent_b, "abort_exhausted")
    agent_b.run(user_question="Explain the repository status")

    assert len(llm_b.calls) == 2, (
        f"an obeyed abort must end the run at plan+synthesis, "
        f"got {len(llm_b.calls)} calls"
    )
    assert len(llm_a.calls) > len(llm_b.calls), (
        "identical state, different verdicts, same path length — "
        "the verdict wire carries no authority"
    )
