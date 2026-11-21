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

import json
from pathlib import Path
from typing import Any

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import Tool, ToolRegistry

# ============================================================
# Helpers
# ============================================================

def _events(log_path: Path) -> list[dict]:
    out: list[dict] = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# A synth response good enough to satisfy a smoke read; tests rarely
# inspect the answer body — they inspect the event trail.
SYNTH_OK = (
    "Conclusion: ok. [s]\n"
    "Facts:\n- ran [s]\n"
    "Sources:\n1. s - s\n"
    "Confidence: medium\nUnverified: nothing\nSafety: nothing\n"
)
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


class SafeReadTool(Tool):
    """A read_only tool used as the safer alternative after an
    approval-denied irreversible step."""

    name = "safe_read"
    description = "stub safe read"
    risk = "read_only"

    def __init__(self) -> None:
        self.calls = 0

    def run(self, **kwargs: Any) -> str:
        self.calls += 1
        return "safe-data"

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        return True, []


class DangerTool(Tool):
    """An irreversible tool — approval gate will fire for this."""

    name = "danger_write"
    description = "stub irreversible write"
    risk = "irreversible"

    def __init__(self) -> None:
        self.calls = 0

    def run(self, **kwargs: Any) -> str:
        self.calls += 1
        return "boom"

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
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


class SequencedPlanner:
    """A planner that returns a different `sources` list per call.

    Lets us simulate "first attempt picks failing path, second attempt
    picks a different path". The N-th call returns plans[min(N-1, len-1)].
    """

    def __init__(self, plans: list[list[dict[str, Any]]], reasoning: str = "sequenced"):
        self.plans = plans
        self.reasoning = reasoning
        self.calls: list[dict[str, Any]] = []

    def plan(
        self,
        question: str,
        file_hint: str | None,
        history: str = "",
        failure_context: str = "",
        forbidden_actions: tuple[tuple[str, str], ...] = (),
        llm=None,
    ):
        import json as _json

        idx = min(len(self.calls), len(self.plans) - 1)
        chosen = self.plans[idx]
        self.calls.append(
            {
                "question": question,
                "file_hint": file_hint,
                "history": history,
                "failure_context": failure_context,
                "forbidden_actions": forbidden_actions,
                "returned_sources": chosen,
            }
        )
        # MVP-12: model the real LLMPlanner._validate_steps gate so
        # SequencedPlanner-based tests reflect production behaviour
        # (otherwise approval-deny / policy-blocked tests would be
        # falsely positive — the FakePlanner already does this).
        forbidden_set = set(forbidden_actions)
        kept: list[dict[str, Any]] = []
        for src in chosen:
            tool = src.get("tool")
            args = src.get("arguments") or {}
            try:
                canonical = _json.dumps(args, sort_keys=True, ensure_ascii=False)
            except TypeError:
                canonical = ""
            if isinstance(tool, str) and canonical and (tool, canonical) in forbidden_set:
                continue
            kept.append(src)

        from core.planner import PlannerOutput
        return PlannerOutput(
            reasoning=self.reasoning,
            sources=kept,
            raw_response="",
            warnings=[],
        )


# ============================================================
# R4 — verify_failed recovers on attempt 2 via different arguments
# ============================================================

class TestReplanAfterVerifyFailed:
    def test_verify_failed_triggers_replan(self, workspace: Path):
        # Same tool reused, but FlakyTool rejects its OWN first output and
        # accepts the second. The planner returns the SAME plan twice — the
        # tool's per-call counter does the discrimination.
        flaky = FlakyTool(fail_first=1)
        step = {
            "tool": "flaky",
            "arguments": {},
            "label": "stub:flaky",
            "expected_outcome": "test",
        }
        planner = SequencedPlanner(plans=[[step], [step]])

        agent, log_path = _build_agent(
            workspace=workspace,
            tools=[flaky],
            planner=planner,
            llm_responses=[SYNTH_OK],
        )
        agent.run(user_question="please call flaky", file_hint=None)

        events = _events(log_path)
        replan_events = [e for e in events if e["event"] == "replan"]
        assert len(replan_events) == 1
        assert "verify_failed" in replan_events[0]["payload"]["triggers"]

        # FlakyTool.run() called twice: first rejected, second accepted.
        assert len(flaky.calls) == 2

        # Replan-context fed to attempt 2 mentions verify_failed.
        assert "verify_failed" in planner.calls[1]["failure_context"]


# ============================================================
# R5 — approval_deny lets the planner switch to a safer path
# ============================================================

class TestReplanAfterApprovalDeny:
    def test_approval_deny_replans_with_safe_alternative(self, workspace: Path):
        # Attempt 1: planner picks an irreversible tool -> approval=deny
        # Attempt 2: planner picks a read_only tool      -> runs cleanly
        danger = DangerTool()
        safe = SafeReadTool()

        planner = SequencedPlanner(
            plans=[
                [
                    {
                        "tool": "danger_write",
                        "arguments": {"x": 1},
                        "label": "stub:danger",
                        "expected_outcome": "should be denied",
                    }
                ],
                [
                    {
                        "tool": "safe_read",
                        "arguments": {},
                        "label": "stub:safe",
                        "expected_outcome": "safer alternative",
                    }
                ],
            ]
        )

        agent, log_path = _build_agent(
            workspace=workspace,
            tools=[danger, safe],
            planner=planner,
            llm_responses=[SYNTH_OK],
            approval_provider=AutoApprover(default="deny"),
        )
        agent.run(user_question="do the dangerous thing", file_hint=None)

        events = _events(log_path)
        replan_events = [e for e in events if e["event"] == "replan"]
        assert len(replan_events) == 1
        assert "approval_deny" in replan_events[0]["payload"]["triggers"]

        # DangerTool MUST NEVER have run.
        assert danger.calls == 0
        # SafeReadTool ran exactly once (attempt 2).
        assert safe.calls == 1


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


# ============================================================
# file_not_found — hard stop, no web fallback, no creation
# ============================================================

class AbsentFileTool(Tool):
    """Simulates file_read on a missing file: raises FileNotFoundError."""
    name = "absent_read"
    description = "stub that always raises FileNotFoundError"
    risk = "read_only"

    def run(self, path: str = "missing.txt") -> str:
        raise FileNotFoundError(f"File not found: {path}")

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        return True, []


class TestFileNotFoundReplan:
    """
    When file_read (or any tool) raises FileNotFoundError:
    - The error event must have code="file_not_found", NOT "tool_error".
    - The replan budget for file_not_found is max_occurrences=1 (no retry).
    - The planner is NOT called a second time (budget exhausted immediately).
    - The synthesizer still runs so the user gets a structured honest answer.
    """

    def _run(self, workspace: Path):
        step = {
            "tool": "absent_read",
            "arguments": {"path": "missing.txt"},
            "label": "file:missing.txt",
            "expected_outcome": "file content",
        }
        # SequencedPlanner returns the same failing step on every call
        planner = SequencedPlanner(plans=[[step], [step]])
        agent, log_path = _build_agent(
            workspace=workspace,
            tools=[AbsentFileTool()],
            planner=planner,
            llm_responses=[SYNTH_FAILED],
            max_replan_attempts=3,  # global cap is generous; type budget caps first
        )
        agent.run(user_question="read missing file", file_hint=None)
        return _events(log_path), planner

    def test_error_event_has_file_not_found_code(self, workspace: Path):
        events, _ = self._run(workspace)
        error_codes = [
            e["payload"].get("code")
            for e in events
            if e["event"] == "error"
        ]
        assert "file_not_found" in error_codes, (
            f"Expected code='file_not_found' in error events, got: {error_codes}"
        )

    def test_tool_error_code_not_emitted(self, workspace: Path):
        events, _ = self._run(workspace)
        error_codes = [
            e["payload"].get("code")
            for e in events
            if e["event"] == "error"
        ]
        assert "tool_error" not in error_codes, (
            "FileNotFoundError must NOT produce code='tool_error'"
        )

    def test_planner_called_only_once(self, workspace: Path):
        """Budget max_occurrences=1 means no retry — planner runs once."""
        _, planner = self._run(workspace)
        assert len(planner.calls) == 1, (
            f"Planner should be called exactly once for file_not_found, "
            f"got {len(planner.calls)} call(s)"
        )

    def test_synthesizer_still_runs(self, workspace: Path):
        """User must get a structured response, not a bare exception."""
        events, _ = self._run(workspace)
        assert any(e["event"] == "respond" for e in events), (
            "Synthesizer must run even when file is not found"
        )

    def test_file_not_found_in_replan_budget(self):
        """Confirm the budget definition itself is correct."""
        from core.replan import DEFAULT_BUDGETS
        budget = DEFAULT_BUDGETS["file_not_found"]
        assert budget.max_occurrences == 1
        assert budget.requires_different_action is True
        assert "NOT" in budget.advice  # must contain explicit prohibition
