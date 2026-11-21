"""C08 — the actuation gateway's two REFUSAL outcomes, bitten through the loop.

The gateway class has its own suite; the loop's obedience to its verdict did
not. Measured 2026-08-08:

- M52 (the `deny` branch skipped) and M53 (the `block` branch skipped) both
  left every gateway/actuation/policy/safety/approval test green. Only the
  `simulate` outcome had an end-to-end observer
  (test_actuation_gateway.py:test_loop_effectful_dry_run...).

This is the effectful path — the tools that change the world — so a refusal
that the loop fails to obey is worse than the same hole on the read-only
side, which M51 already covers.

Both tests assert the same three things, because a refusal is not one fact:
the effect must NOT happen, the run must journal the refusal under its own
code, and the failure must reach the replan machinery rather than vanish.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_write import FileWriteTool

TARGET = "gateway_refusal_probe.txt"


class _LatchedState:
    """Minimal stand-in for an engaged kill switch."""

    active = True
    reason = "probe hard stop"
    window = "day"
    counter = "cost"


class _LatchedKillSwitch:
    def status(self, snapshot=None):
        return _LatchedState()


def _agent(workspace: Path) -> tuple[AgentLoop, Path]:
    registry = ToolRegistry()
    registry.register(FileWriteTool(workspace_root=workspace))
    planner = FakePlanner(
        sources=[{
            "tool": "file_write",
            "arguments": {"path": TARGET, "content": "written despite refusal"},
            "label": f"file_write:{TARGET}",
            "expected_outcome": "writes the file",
        }]
    )
    trace_id = new_trace_id()
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=["answer"] * 4),
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False),
        planner=planner,
        # Approval says yes, so nothing but the gateway can stop the effect.
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
        clarification_enabled=False,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


def _events(log_path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_a_gateway_hard_stop_prevents_the_write(workspace: Path) -> None:
    """`block` — an engaged kill switch. The effect must not reach the disk."""
    agent, log_path = _agent(workspace)
    agent.gateway_kill_switch = _LatchedKillSwitch()

    agent.run("write the file")

    assert not (workspace / TARGET).exists(), (
        "the gateway hard-stopped and the file was written anyway — the loop "
        "does not obey the block verdict"
    )
    events = _events(log_path)
    outcomes = [
        e["payload"].get("outcome")
        for e in events if e["event"] == "gateway_decision"
    ]
    assert "block" in outcomes, "the hard stop must be journaled as such"
    codes = [e["payload"].get("code") for e in events if e["event"] == "error"]
    assert "gateway_blocked" in codes, (
        "a hard stop must surface under its own error code, not as a generic failure"
    )
    assert "replan_exhausted" in codes, (
        "the blocked step must reach the replan machinery — a refusal that "
        "leaves no trigger is a silent failure"
    )


def test_a_gateway_deny_prevents_the_write(workspace: Path) -> None:
    """`deny` — the gateway's policy consult refuses this effectful action.

    The deny verdict is produced by the gateway's own policy consult; here it
    is forced at the seam so the test asserts the LOOP's obedience rather than
    re-deriving when a policy should deny (that is the gateway suite's job).
    """
    import core.actuation_gateway as gw_mod

    agent, log_path = _agent(workspace)
    real_evaluate = gw_mod.ActuationGateway.evaluate

    def denying_evaluate(self, action, *, registry=None):
        verdict = real_evaluate(self, action, registry=registry)
        from core.models import PolicyDecision

        return gw_mod.GatewayDecision(
            outcome="deny",
            tool_name=verdict.tool_name,
            path=verdict.path,
            policy=PolicyDecision(
                policy_id="probe",
                subject=action.tool_name or "file_write",
                action=action.id,
                decision="deny",
                reasons=["probe: gateway policy refused"],
            ),
            reasons=["probe: gateway policy refused"],
        )

    gw_mod.ActuationGateway.evaluate = denying_evaluate
    try:
        agent.run("write the file")
    finally:
        gw_mod.ActuationGateway.evaluate = real_evaluate

    assert not (workspace / TARGET).exists(), (
        "the gateway denied and the file was written anyway — on the effectful "
        "path the shared deny handler is NOT reached, so this branch is the "
        "only thing standing between a refusal and the disk"
    )
    events = _events(log_path)
    codes = [e["payload"].get("code") for e in events if e["event"] == "error"]
    assert "policy_blocked" in codes, "a gateway deny must surface as policy_blocked"
    assert "replan_exhausted" in codes, (
        "the denied step must reach the replan machinery"
    )
