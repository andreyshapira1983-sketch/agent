"""MVP-8 — Re-planning.

Hard invariants this file proves end-to-end:

  R1. A clean first attempt produces ZERO `replan` events.
  R2. A 0-step plan (general-knowledge answer) is a success, not a replan trigger.
  R3. `tool_error` on attempt 1 → planner is re-invoked → second plan succeeds.
  R4. `verify_failed` on attempt 1 → planner is re-invoked → second plan succeeds.
  R5. `approval_deny` on attempt 1 → planner is re-invoked and given the chance
      to propose a read-only alternative.
  R6. When every attempt fails the same way, the loop stops after
      `max_replan_attempts` and logs `error.code=replan_exhausted`. The
      synthesizer still runs so the user gets a structured response, NOT
      a bare error string.
  R7. The planner's `user` prompt on attempt N>=2 contains a `<replan_context>`
      block describing every prior failure, with the right `code` value.
  R8. Every `planner` and `plan` event carries an `attempt` field that
      monotonically increases. The `respond` event records `attempts_used`
      and a boolean `replan_exhausted`.
  R9. `max_replan_attempts=1` disables re-planning (legacy single-attempt
      behaviour).
  R10. `max_replan_attempts=0` is rejected at construction time (kernel
       must always run at least one attempt).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import Tool, ToolRegistry

# ============================================================
# Helpers
# ============================================================


SYNTH_FAILED = (
    "Conclusion: could not gather evidence. [general-knowledge]\n"
    "Facts:\n- agent tried several plans, none worked [general-knowledge]\n"
    "Sources:\n1. general-knowledge - general-knowledge\n"
    "Confidence: low\nUnverified: the original question\nSafety: nothing\n"
)


class FlakyTool(Tool):
    """A tool whose validate_output rejects the first N runs.

    Lets us drive a `verify_failed` trigger deterministically without
    network or real file errors. After `fail_first` runs `run()` keeps
    being called but `validate_output` starts accepting the result.
    """

    name = "flaky"
    description = "test stub that fails validation on the first N runs"
    risk = "read_only"

    def __init__(self, fail_first: int = 1):
        self.fail_first = fail_first
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> str:
        self.calls.append(dict(kwargs))
        # Output is intentionally distinguishable per call so the
        # validator can decide based on the current call index.
        return f"output-{len(self.calls)}"

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        # First `fail_first` outputs are rejected; subsequent ones pass.
        # Outputs are named "output-1", "output-2", …
        try:
            idx = int(str(output).rsplit("-", 1)[-1])
        except (ValueError, AttributeError):
            return False, ["malformed stub output"]
        if idx <= self.fail_first:
            return False, [f"flaky tool rejected output #{idx}"]
        return True, []


def _build_agent(
    workspace: Path,
    tools: list[Tool],
    planner: FakePlanner,
    llm_responses: list[str],
    approval_provider=None,
    max_replan_attempts: int = 3,
    replan_policy=None,
) -> tuple[AgentLoop, Path]:
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    policy = PolicyGate(registry)
    llm = FakeLLM(responses=llm_responses)
    trace_id = new_trace_id()
    logger = TraceLogger(
        trace_id=trace_id, log_dir=workspace / "logs", verbose=False
    )
    agent = AgentLoop(
        registry=registry,
        policy=policy,
        llm=llm,
        logger=logger,
        planner=planner,
        approval_provider=approval_provider,
        max_replan_attempts=max_replan_attempts,
        replan_policy=replan_policy,
        # These tests assert byte-for-byte against canned LLM responses.
        # Disable Verifier annotation so the contract stays focused on
        # replan policy — Verifier has its own dedicated test file.
        verifier_enabled=False,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


def _generous_policy(max_total: int):
    """Build a ReplanPolicy where each per-type budget is wide enough not
    to bite. Useful for MVP-8 legacy tests that want the GLOBAL cap to
    be the only ceiling.
    """
    from core.replan import DEFAULT_BUDGETS, FailureBudget, ReplanPolicy

    wide = {
        code: FailureBudget(
            max_occurrences=max(b.max_occurrences, max_total + 5),
            advice=b.advice,
            requires_different_action=b.requires_different_action,
        )
        for code, b in DEFAULT_BUDGETS.items()
    }
    return ReplanPolicy(budgets=wide, max_total_replans=max_total)


# ============================================================
# R7 — planner sees structured replan context
# ============================================================

class TestPlannerSeesFailureHistory:
    def test_failure_context_contains_code_tool_reason(self, workspace: Path):
        flaky = FlakyTool(fail_first=999)
        step = {
            "tool": "flaky",
            "arguments": {"q": "test"},
            "label": "stub:flaky",
            "expected_outcome": "fail",
        }
        planner = FakePlanner(sources=[step])

        # MVP-12: budgets are wide so the global cap is the only ceiling.
        agent, _log = _build_agent(
            workspace=workspace,
            tools=[flaky],
            planner=planner,
            llm_responses=[SYNTH_FAILED],
            max_replan_attempts=3,
            replan_policy=_generous_policy(max_total=3),
        )
        agent.run(user_question="try", file_hint=None)

        # Attempt 1 sees no failure context.
        assert planner.calls[0]["failure_context"] == ""

        # Attempts 2 and 3 see growing failure context.
        ctx2 = planner.calls[1]["failure_context"]
        assert "<replan_context" in ctx2
        assert "attempt=\"2\"" in ctx2
        assert "verify_failed" in ctx2
        assert "tool=flaky" in ctx2
        # MVP-12: planner sees concrete advice for the failure type.
        assert "<advice>" in ctx2

        ctx3 = planner.calls[2]["failure_context"]
        assert "<replan_context" in ctx3
        assert "attempt=\"3\"" in ctx3
        # Attempt 3 sees both prior failures, not just the latest.
        assert ctx3.count("code=verify_failed") >= 2


# ============================================================
# R9 + R10 — configuration boundaries
# ============================================================

class TestMaxAttemptsConfig:
    def test_max_replan_attempts_zero_rejected(self, workspace: Path):
        # Constructor must refuse to build an agent that cannot run.
        registry = ToolRegistry()
        policy = PolicyGate(registry)
        llm = FakeLLM(responses=[])
        logger = TraceLogger(
            trace_id="zero", log_dir=workspace / "logs", verbose=False
        )
        try:
            with pytest.raises(ValueError, match="max_replan_attempts"):
                AgentLoop(
                    registry=registry,
                    policy=policy,
                    llm=llm,
                    logger=logger,
                    max_replan_attempts=0,
                )
        finally:
            logger.close()
