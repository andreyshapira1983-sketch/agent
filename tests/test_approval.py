"""MVP-6 — Approval Request UX.

Covers all seven acceptance criteria from the spec:

  1. read_only tool runs without asking.
  2. irreversible tool triggers an approval_request.
  3. user says yes -> tool executes.
  4. user says no -> tool does NOT execute.
  5. empty / unrecognised / EOF -> abort, tool does NOT execute.
  6. JSONL trace contains approval_request + approval_decision events.
  7. Without an approval provider configured, an escalated tool is refused
     outright (no tool_call, no tool_result, no verify).

Plus unit tests for `_classify`, `CLIApprovalProvider`, and `AutoApprover`.

The tests use `RecordingTool` — a safe, in-memory tool that increments a
counter on `run()`. We never wire a real irreversible tool: the agent must
prove the approval gate works on something harmless before any dangerous
tool is ever registered.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import pytest

from core.approval import (
    AutoApprover,
    CLIApprovalProvider,
    _classify,
)
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.models import ApprovalDecision, ApprovalRequest
from core.policy import PolicyGate
from tools.base import Tool, ToolRegistry
from tests.conftest import FakeLLM, FakePlanner


# ============================================================
# Safe tool with a side-effect counter
# ============================================================

class RecordingTool(Tool):
    """A tool that just counts its `run` invocations.

    Configurable `risk` lets the same fixture exercise both `read_only`
    (no approval gate) and `irreversible` (approval gate) branches.
    """

    def __init__(self, name: str, risk: Literal["read_only", "reversible", "irreversible", "external"]):
        self.name = name
        self.description = f"recording:{name}"
        self.risk = risk
        self.runs: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> Any:
        self.runs.append(kwargs)
        return f"ok-{len(self.runs)}"


# ============================================================
# Helpers
# ============================================================

def _events(log_path: Path) -> list[dict]:
    events = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _build_loop(
    workspace: Path,
    tool: Tool,
    planned_step: dict[str, Any],
    approval_provider=None,
) -> tuple[AgentLoop, Path]:
    registry = ToolRegistry()
    registry.register(tool)
    policy = PolicyGate(registry)
    planner = FakePlanner(sources=[planned_step])
    llm = FakeLLM(
        responses=[
            "Conclusion: ok. [stub:t]\nFacts:\n- ran [stub:t]\n"
            "Sources:\n1. stub:t - t\nConfidence: medium\nUnverified: nothing\n"
        ]
    )
    trace_id = new_trace_id()
    logger = TraceLogger(
        trace_id=trace_id,
        log_dir=workspace / "logs",
        verbose=False,
    )
    agent = AgentLoop(
        registry=registry,
        policy=policy,
        llm=llm,
        logger=logger,
        planner=planner,
        memory=None,
        approval_provider=approval_provider,
        # The approval-gate tests assert single-attempt event counts;
        # post-MVP-8 default is 3 replan attempts. Pin to 1 here.
        # MVP-8 re-planning is exercised separately in test_replan.py.
        max_replan_attempts=1,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


def _step_for(tool_name: str, label: str = "stub:t") -> dict[str, Any]:
    return {
        "tool": tool_name,
        "arguments": {"x": 1},
        "label": label,
        "expected_outcome": "test step",
    }


# ============================================================
# Unit: _classify
# ============================================================

class TestClassify:
    @pytest.mark.parametrize("raw", ["y", "yes", "YES", "  Yes  ", "д", "да", "Да", "approve", "ok", "okay"])
    def test_yes_tokens_become_approve(self, raw):
        decision, reason = _classify(raw)
        assert decision == "approve"
        assert "input=" in reason

    @pytest.mark.parametrize("raw", ["n", "no", "NO", "нет", "Нет", "deny", "cancel"])
    def test_no_tokens_become_deny(self, raw):
        decision, _ = _classify(raw)
        assert decision == "deny"

    @pytest.mark.parametrize("raw", ["", "   ", None, "maybe", "yolo", "?", "garbage", "y es"])
    def test_other_inputs_become_abort(self, raw):
        decision, reason = _classify(raw)
        assert decision == "abort"
        assert reason  # reason is always populated


# ============================================================
# Unit: CLIApprovalProvider
# ============================================================

def _request_fixture() -> ApprovalRequest:
    return ApprovalRequest(
        action_id="act_test",
        step_id="step_test",
        tool_name="dangerous_write",
        arguments={"path": "/tmp/x"},
        risk="irreversible",
        reasons=["risk=irreversible"],
        summary="please confirm",
    )


class TestCLIApprovalProvider:
    def test_yes_returns_approve_decision(self):
        provider = CLIApprovalProvider(input_fn=lambda _prompt: "yes")
        decision = provider.request(_request_fixture())
        assert isinstance(decision, ApprovalDecision)
        assert decision.decision == "approve"
        assert decision.responder == "user"
        assert decision.raw_input == "yes"

    def test_no_returns_deny_decision(self):
        provider = CLIApprovalProvider(input_fn=lambda _prompt: "no")
        decision = provider.request(_request_fixture())
        assert decision.decision == "deny"
        assert decision.responder == "user"

    def test_empty_returns_abort_with_timeout_responder(self):
        provider = CLIApprovalProvider(input_fn=lambda _prompt: "")
        decision = provider.request(_request_fixture())
        assert decision.decision == "abort"
        assert decision.responder == "timeout"

    def test_eof_returns_abort(self):
        def raises_eof(_prompt: str) -> str:
            raise EOFError()
        provider = CLIApprovalProvider(input_fn=raises_eof)
        decision = provider.request(_request_fixture())
        assert decision.decision == "abort"
        assert decision.raw_input is None

    def test_keyboard_interrupt_returns_abort(self):
        def raises_kbi(_prompt: str) -> str:
            raise KeyboardInterrupt()
        provider = CLIApprovalProvider(input_fn=raises_kbi)
        decision = provider.request(_request_fixture())
        assert decision.decision == "abort"
        assert decision.responder == "timeout"

    def test_request_id_round_trips(self):
        req = _request_fixture()
        provider = CLIApprovalProvider(input_fn=lambda _prompt: "yes")
        decision = provider.request(req)
        assert decision.request_id == req.id


# ============================================================
# Unit: AutoApprover
# ============================================================

class TestAutoApprover:
    @pytest.mark.parametrize("default", ["approve", "deny", "abort"])
    def test_default_drives_decision(self, default):
        provider = AutoApprover(default=default)
        decision = provider.request(_request_fixture())
        assert decision.decision == default
        assert decision.responder == "auto"

    def test_records_calls(self):
        provider = AutoApprover()
        req1 = _request_fixture()
        req2 = _request_fixture()
        provider.request(req1)
        provider.request(req2)
        assert len(provider.calls) == 2
        assert provider.calls[0].id == req1.id


# ============================================================
# Integration: the Control Loop with approval wired in
# ============================================================

# ---------- Acceptance #1 — read_only runs without an approval gate ----------

class TestReadOnlySkipsApproval:
    def test_read_only_tool_runs_without_approval_event(self, workspace: Path):
        tool = RecordingTool("safe_reader", "read_only")
        # Even with an approver wired, a read_only action must not trigger it.
        approver = AutoApprover(default="deny")  # would block if asked
        agent, log_path = _build_loop(
            workspace,
            tool,
            _step_for("safe_reader"),
            approval_provider=approver,
        )

        agent.run(user_question="please read", file_hint=None)

        assert tool.runs == [{"x": 1}]  # tool actually executed
        assert approver.calls == []     # approver never invoked

        events = _events(log_path)
        names = [e["event"] for e in events]
        assert "approval_request" not in names
        assert "approval_decision" not in names
        # Standard execute path went through normally.
        assert "tool_call" in names
        assert "tool_result" in names
        assert "verify" in names


# ---------- Acceptance #2, #3, #6 — escalate -> approve -> tool runs ----------

class TestApproveAllowsExecution:
    def test_irreversible_tool_with_approve_runs(self, workspace: Path):
        tool = RecordingTool("dangerous_write", "irreversible")
        approver = AutoApprover(default="approve")
        agent, log_path = _build_loop(
            workspace,
            tool,
            _step_for("dangerous_write"),
            approval_provider=approver,
        )

        agent.run(user_question="please write", file_hint=None)

        assert tool.runs == [{"x": 1}], "tool MUST run when approval=approve"
        assert len(approver.calls) == 1
        req = approver.calls[0]
        assert req.tool_name == "dangerous_write"
        assert req.risk == "irreversible"
        assert req.arguments == {"x": 1}

        events = _events(log_path)
        names = [e["event"] for e in events]

        # Acceptance #6: both approval events must be present
        assert "approval_request" in names
        assert "approval_decision" in names
        # ...and the order must be policy -> approval_request -> approval_decision -> tool_call
        idx = {n: names.index(n) for n in
               ["policy", "approval_request", "approval_decision", "tool_call"]}
        assert idx["policy"] < idx["approval_request"] < idx["approval_decision"] < idx["tool_call"]

        approval_request = next(e for e in events if e["event"] == "approval_request")
        assert approval_request["payload"]["risk"] == "irreversible"
        assert approval_request["payload"]["tool_name"] == "dangerous_write"
        assert approval_request["payload"]["arguments"] == {"x": 1}
        assert approval_request["payload"]["policy_decision_id"]  # linked to the policy event

        approval_decision = next(e for e in events if e["event"] == "approval_decision")
        assert approval_decision["payload"]["decision"] == "approve"
        assert approval_decision["payload"]["responder"] == "auto"
        assert approval_decision["payload"]["request_id"] == approval_request["payload"]["id"]


# ---------- Acceptance #4, #7 — escalate -> deny -> tool does NOT run ----------

class TestDenyBlocksExecution:
    def test_irreversible_tool_with_deny_does_not_run(self, workspace: Path):
        tool = RecordingTool("dangerous_write", "irreversible")
        approver = AutoApprover(default="deny")
        agent, log_path = _build_loop(
            workspace,
            tool,
            _step_for("dangerous_write"),
            approval_provider=approver,
        )

        agent.run(user_question="please write", file_hint=None)

        # Acceptance #4 + #7: the tool MUST NOT have run.
        assert tool.runs == [], "tool MUST NOT run when approval=deny"
        assert len(approver.calls) == 1

        events = _events(log_path)
        names = [e["event"] for e in events]

        assert "approval_request" in names
        assert "approval_decision" in names

        # Hard invariant: nothing past the gate fires.
        assert "tool_call" not in names
        assert "tool_result" not in names
        assert "verify" not in names

        errors = [e for e in events if e["event"] == "error"]
        assert any(ev["payload"]["code"] == "approval_deny" for ev in errors), [
            ev["payload"]["code"] for ev in errors
        ]


# ---------- Acceptance #5 — abort -> tool does NOT run ----------

class TestAbortBlocksExecution:
    def test_irreversible_tool_with_abort_does_not_run(self, workspace: Path):
        tool = RecordingTool("dangerous_write", "irreversible")
        approver = AutoApprover(default="abort")
        agent, log_path = _build_loop(
            workspace,
            tool,
            _step_for("dangerous_write"),
            approval_provider=approver,
        )

        agent.run(user_question="please write", file_hint=None)

        assert tool.runs == []
        assert len(approver.calls) == 1

        events = _events(log_path)
        names = [e["event"] for e in events]
        assert "approval_request" in names
        assert "approval_decision" in names
        assert "tool_call" not in names

        errors = [e for e in events if e["event"] == "error"]
        assert any(ev["payload"]["code"] == "approval_abort" for ev in errors), [
            ev["payload"]["code"] for ev in errors
        ]

    def test_empty_user_input_via_cli_provider_is_abort(self, workspace: Path):
        """End-to-end: feed the CLIApprovalProvider an empty string and
        prove the tool still does not run."""
        tool = RecordingTool("dangerous_write", "irreversible")
        provider = CLIApprovalProvider(input_fn=lambda _prompt: "")
        agent, log_path = _build_loop(
            workspace,
            tool,
            _step_for("dangerous_write"),
            approval_provider=provider,
        )

        agent.run(user_question="please write", file_hint=None)

        assert tool.runs == []

        events = _events(log_path)
        decisions = [e for e in events if e["event"] == "approval_decision"]
        assert len(decisions) == 1
        assert decisions[0]["payload"]["decision"] == "abort"
        assert decisions[0]["payload"]["responder"] == "timeout"

    def test_unrecognised_input_via_cli_provider_is_abort(self, workspace: Path):
        tool = RecordingTool("dangerous_write", "irreversible")
        provider = CLIApprovalProvider(input_fn=lambda _prompt: "maybe later")
        agent, log_path = _build_loop(
            workspace,
            tool,
            _step_for("dangerous_write"),
            approval_provider=provider,
        )

        agent.run(user_question="please write", file_hint=None)

        assert tool.runs == []
        decisions = [
            e for e in _events(log_path) if e["event"] == "approval_decision"
        ]
        assert decisions[0]["payload"]["decision"] == "abort"


# ---------- Acceptance #7 (alt) — no provider wired -> refuse ----------

class TestNoProviderRefuses:
    """When no approval provider is configured at all, the loop must
    treat escalated actions as `approval_unavailable` and refuse.

    Re-stated and re-tested here so this guarantee is co-located with
    the rest of the approval test suite, not only in test_policy.py.
    """

    def test_escalated_tool_with_no_provider_is_refused(self, workspace: Path):
        tool = RecordingTool("dangerous_write", "irreversible")
        agent, log_path = _build_loop(
            workspace,
            tool,
            _step_for("dangerous_write"),
            approval_provider=None,  # the key bit
        )

        agent.run(user_question="please write", file_hint=None)

        assert tool.runs == []
        events = _events(log_path)
        names = [e["event"] for e in events]
        # No request was made because there is nobody to ask.
        assert "approval_request" not in names
        assert "approval_decision" not in names
        assert "tool_call" not in names
        errors = [e for e in events if e["event"] == "error"]
        assert any(e["payload"]["code"] == "approval_unavailable" for e in errors)


# ---------- Bonus — external risk also triggers approval ----------

class TestExternalRiskAlsoEscalates:
    def test_external_tool_requires_approval(self, workspace: Path):
        tool = RecordingTool("external_call", "external")
        approver = AutoApprover(default="deny")
        agent, log_path = _build_loop(
            workspace,
            tool,
            _step_for("external_call"),
            approval_provider=approver,
        )

        agent.run(user_question="reach the network", file_hint=None)

        assert tool.runs == []
        assert len(approver.calls) == 1
        assert approver.calls[0].risk == "external"
