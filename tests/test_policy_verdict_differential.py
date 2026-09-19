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

import json
from pathlib import Path

from core.model_usage import ModelUsageLimits
from core.replan import ReplanDecision
from tests.conftest import FakeLLM
from tests.test_budget_resume import _build_guarded_agent

CITING_ANSWER = "The fact [web:http://example.com/a] holds."
PLAIN_ANSWER = "Plain answer, no citations."
PLAN_NO_TOOLS = '{"reasoning":"no tools","sources":[]}'


def _agent(workspace: Path, *, answer: str):
    llm = FakeLLM(responses=[PLAN_NO_TOOLS, answer])
    agent = _build_guarded_agent(
        workspace, llm, ModelUsageLimits(),
        verifier_enabled=True, max_replan_attempts=3,
    )
    return agent, llm


def _spy_on_decide(agent) -> list[dict]:
    calls: list[dict] = []
    real = agent.replan_policy.decide

    def spy(failure_history, completed_attempts):
        calls.append({"completed_attempts": completed_attempts})
        return real(
            failure_history=failure_history, completed_attempts=completed_attempts
        )

    agent.replan_policy.decide = spy
    return calls


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


def _events(agent) -> list[dict]:
    return [
        json.loads(line)
        for line in agent.log.path.read_text(encoding="utf-8").splitlines()
    ]


# ── the admission gate, both poles, in isolation ──────────────────────────────

def test_without_unresolved_citations_the_policy_is_never_consulted(
    workspace: Path,
) -> None:
    agent, _llm = _agent(workspace, answer=PLAIN_ANSWER)
    calls = _spy_on_decide(agent)

    agent.run(user_question="Explain the repository status")

    assert calls == [], (
        "no failure, no unresolved citation — nothing may consult the policy"
    )


def test_an_unresolved_citation_admits_the_policy(workspace: Path) -> None:
    """The pole the gate mutation must redden: signal present -> consulted."""
    agent, _llm = _agent(workspace, answer=CITING_ANSWER)
    calls = _spy_on_decide(agent)

    agent.run(user_question="Explain the repository status")

    assert calls, "an unresolved citation must reach ReplanPolicy.decide"


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


def test_abort_no_retry_stops_the_run_and_only_the_journal_tells_it_apart(
    workspace: Path,
) -> None:
    """The pole never observed on this path before.

    Downstream it is IDENTICAL to abort_exhausted by construction — both fall
    into the same non-continue branch — so the assertion is honest about
    that: same stopping consequence, distinction preserved only as
    `decision_action` in the replan_exhausted event.
    """
    agent, llm = _agent(workspace, answer=CITING_ANSWER)
    _force_verdict(agent, "abort_no_retry")

    agent.run(user_question="Explain the repository status")

    assert len(llm.calls) == 2, "abort_no_retry must stop the verify loop too"
    exhausted = [e for e in _events(agent) if e.get("event") == "replan_exhausted"]
    assert exhausted, "the stop must be journaled"
    payload = json.dumps(exhausted[-1])
    assert "abort_no_retry" in payload, (
        "the journal is the only place the two abort flavours differ; "
        "losing decision_action erases the distinction entirely"
    )
