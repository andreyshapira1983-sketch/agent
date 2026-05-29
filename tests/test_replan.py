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
from typing import Any, Literal

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tools.base import Tool, ToolRegistry
from tools.file_read import FileReadTool
from tests.conftest import FakeLLM, FakePlanner


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
# R1 + R2 — no replan on success
# ============================================================

class TestNoReplanOnSuccess:
    def test_clean_first_attempt_emits_no_replan_event(self, workspace: Path):
        (workspace / "doc.txt").write_text("hello", encoding="utf-8")
        planner = FakePlanner(
            sources=[
                {
                    "tool": "file_read",
                    "arguments": {"path": "doc.txt"},
                    "label": "file:doc.txt",
                    "expected_outcome": "file content",
                }
            ]
        )
        agent, log_path = _build_agent(
            workspace=workspace,
            tools=[FileReadTool(workspace_root=workspace)],
            planner=planner,
            llm_responses=[SYNTH_OK],
        )
        agent.run(user_question="what is in doc.txt?", file_hint="doc.txt")

        events = _events(log_path)
        assert not any(e["event"] == "replan" for e in events)
        assert not any(
            e["event"] == "error" and e["payload"].get("code") == "replan_exhausted"
            for e in events
        )

        # Planner is called exactly once.
        assert len(planner.calls) == 1
        # And the planner's first call sees no failure context.
        assert planner.calls[0]["failure_context"] == ""

    def test_empty_plan_is_success_no_replan(self, workspace: Path):
        # Planner picks NO tools (general-knowledge answer). Not a failure.
        planner = FakePlanner(sources=[])
        agent, log_path = _build_agent(
            workspace=workspace,
            tools=[FileReadTool(workspace_root=workspace)],
            planner=planner,
            llm_responses=[SYNTH_OK],
        )
        agent.run(user_question="what is 2+2?", file_hint=None)

        events = _events(log_path)
        assert not any(e["event"] == "replan" for e in events)
        # Planner runs exactly once — a 0-step plan is a terminal valid plan.
        assert len(planner.calls) == 1


# ============================================================
# R3 — tool_error recovers on attempt 2
# ============================================================

class TestReplanAfterToolError:
    def test_tool_error_triggers_replan_then_recovers(self, workspace: Path):
        (workspace / "real.txt").write_text("real content", encoding="utf-8")

        # Attempt 1: planner picks a non-existent path -> file_read errors out
        # Attempt 2: planner picks the real path -> success
        planner = SequencedPlanner(
            plans=[
                [
                    {
                        "tool": "file_read",
                        "arguments": {"path": "real.txt", "max_bytes": 10_000},
                        "label": "file:missing.txt",
                        "expected_outcome": "should fail",
                    }
                ],
                [
                    {
                        "tool": "file_read",
                        "arguments": {"path": "real.txt"},
                        "label": "file:real.txt",
                        "expected_outcome": "should succeed",
                    }
                ],
            ]
        )

        # First plan deliberately uses arguments file_read doesn't accept
        # (max_bytes), forcing a TypeError at run time -> tool_error.
        agent, log_path = _build_agent(
            workspace=workspace,
            tools=[FileReadTool(workspace_root=workspace)],
            planner=planner,
            llm_responses=[SYNTH_OK],
        )
        agent.run(user_question="read the file", file_hint="real.txt")

        events = _events(log_path)
        replan_events = [e for e in events if e["event"] == "replan"]
        assert len(replan_events) == 1, [
            (e["event"], e.get("payload", {}).get("code"))
            for e in events
            if e["event"] in {"replan", "error"}
        ]
        assert replan_events[0]["payload"]["attempt"] == 1
        assert replan_events[0]["payload"]["next_attempt"] == 2
        assert "tool_error" in replan_events[0]["payload"]["triggers"]

        # Planner called twice — once per attempt.
        assert len(planner.calls) == 2
        # Attempt 2's planner call saw the failure context.
        assert "<replan_context" in planner.calls[1]["failure_context"]
        assert "tool_error" in planner.calls[1]["failure_context"]

        # And no replan_exhausted error — the second attempt succeeded.
        codes = [
            e["payload"].get("code")
            for e in events
            if e["event"] == "error"
        ]
        assert "replan_exhausted" not in codes


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
# R6 — bounded by max_replan_attempts
# ============================================================

class TestReplanIsBounded:
    def test_repeated_failure_stops_at_max_attempts(self, workspace: Path):
        # Every attempt picks the same broken plan; FlakyTool rejects
        # FOREVER (fail_first=999). After 3 attempts the loop must stop.
        flaky = FlakyTool(fail_first=999)
        step = {
            "tool": "flaky",
            "arguments": {},
            "label": "stub:flaky",
            "expected_outcome": "always fail",
        }
        planner = FakePlanner(sources=[step])

        # MVP-12: pass a generous policy so the per-type budget (which is
        # 2 by default for verify_failed) doesn't stop us before the GLOBAL
        # cap. This test pins the global-cap behaviour specifically.
        agent, log_path = _build_agent(
            workspace=workspace,
            tools=[flaky],
            planner=planner,
            llm_responses=[SYNTH_FAILED],
            max_replan_attempts=3,
            replan_policy=_generous_policy(max_total=3),
        )
        answer = agent.run(user_question="try forever", file_hint=None)

        events = _events(log_path)

        # Exactly N-1 replan events for N attempts (no `replan` event is
        # emitted on the last attempt, only `replan_exhausted`).
        replan_events = [e for e in events if e["event"] == "replan"]
        assert len(replan_events) == 2  # between attempt 1->2 and 2->3

        # The loop emits BOTH `replan_exhausted` and a paired error event.
        exhausted = [
            e for e in events
            if e["event"] == "error" and e["payload"].get("code") == "replan_exhausted"
        ]
        assert len(exhausted) == 1
        assert exhausted[0]["payload"]["context"]["attempts"] == 3

        replan_exhausted_events = [
            e for e in events if e["event"] == "replan_exhausted"
        ]
        assert len(replan_exhausted_events) == 1
        assert replan_exhausted_events[0]["payload"]["attempts"] == 3
        assert replan_exhausted_events[0]["payload"]["decision_action"] == "abort_exhausted"

        # FakePlanner was called exactly 3 times (one per attempt).
        assert len(planner.calls) == 3

        # The synthesizer STILL ran — the user got a structured response,
        # not a bare error string. The FakeLLM canned response above.
        respond_events = [e for e in events if e["event"] == "respond"]
        assert len(respond_events) == 1
        assert respond_events[0]["payload"]["replan_exhausted"] is True
        assert respond_events[0]["payload"]["attempts_used"] == 3
        # Sanity: the synth response reaches the user, then kernel output
        # policy adds an explicit replan-budget warning under Unverified.
        assert "Conclusion: could not gather evidence." in answer
        assert "agent tried several plans, none worked" in answer
        assert "replan budget was exhausted" in answer


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
# R8 — attempt counter monotonically increases in events
# ============================================================

class TestAttemptCounterInEvents:
    def test_planner_and_plan_events_carry_attempt(self, workspace: Path):
        flaky = FlakyTool(fail_first=1)
        step = {
            "tool": "flaky",
            "arguments": {},
            "label": "stub:flaky",
            "expected_outcome": "test",
        }
        planner = FakePlanner(sources=[step])

        agent, log_path = _build_agent(
            workspace=workspace,
            tools=[flaky],
            planner=planner,
            llm_responses=[SYNTH_OK],
            max_replan_attempts=3,
        )
        agent.run(user_question="please", file_hint=None)

        events = _events(log_path)

        planner_events = [e for e in events if e["event"] == "planner"]
        plan_events = [e for e in events if e["event"] == "plan"]

        # Two attempts were needed (fail_first=1).
        assert [e["payload"]["attempt"] for e in planner_events] == [1, 2]
        # Plan events carry attempt in `extra`.
        assert [e["extra"]["attempt"] for e in plan_events] == [1, 2]

    def test_respond_event_reports_attempts_used_on_success(self, workspace: Path):
        (workspace / "x.txt").write_text("hi", encoding="utf-8")
        planner = FakePlanner(
            sources=[
                {
                    "tool": "file_read",
                    "arguments": {"path": "x.txt"},
                    "label": "file:x.txt",
                    "expected_outcome": "content",
                }
            ]
        )
        agent, log_path = _build_agent(
            workspace=workspace,
            tools=[FileReadTool(workspace_root=workspace)],
            planner=planner,
            llm_responses=[SYNTH_OK],
        )
        agent.run(user_question="read x", file_hint="x.txt")

        events = _events(log_path)
        respond = next(e for e in events if e["event"] == "respond")
        assert respond["payload"]["attempts_used"] == 1
        assert respond["payload"]["replan_exhausted"] is False


# ============================================================
# R9 + R10 — configuration boundaries
# ============================================================

class TestMaxAttemptsConfig:
    def test_max_replan_attempts_one_disables_replan(self, workspace: Path):
        flaky = FlakyTool(fail_first=999)
        step = {
            "tool": "flaky",
            "arguments": {},
            "label": "stub:flaky",
            "expected_outcome": "fail",
        }
        planner = FakePlanner(sources=[step])

        agent, log_path = _build_agent(
            workspace=workspace,
            tools=[flaky],
            planner=planner,
            llm_responses=[SYNTH_FAILED],
            max_replan_attempts=1,
        )
        agent.run(user_question="single shot", file_hint=None)

        events = _events(log_path)
        # No replan events at all — only one attempt by contract.
        assert not any(e["event"] == "replan" for e in events)
        # Exhausted immediately.
        exhausted = [
            e for e in events
            if e["event"] == "error" and e["payload"].get("code") == "replan_exhausted"
        ]
        assert len(exhausted) == 1
        # Planner called exactly once.
        assert len(planner.calls) == 1

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
