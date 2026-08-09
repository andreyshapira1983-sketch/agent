"""The gateway's `block` has two producers. Only one of them was ever driven.

`tests/test_gateway_refusals_stop_the_effect_c08.py` certified that the loop
obeys a `block` — through a LATCHED KILL SWITCH, which is what its
`_LatchedKillSwitch` exists for. But `core/gateway_consult.py:
collect_hard_stop_reasons` builds its reason tuple from TWO independent sources:

    kill_switch_active   :118-126   state computed from the budget snapshot
    readiness_blocker    :127-129   gated by check_readiness

The second one was exercised only by `tests/test_actuation_gateway.py:325` and
`tests/test_gateway_consult.py:36`, both of which call the gateway class or the
collector function DIRECTLY and never drive the loop.

MEASURED 2026-08-09. Disabling that producer at its wiring —
`core/loop_step_execution.py:320`, `check_readiness=...` forced to `False`,
leaving the kill-switch producer untouched — let the effectful write through:
the gateway returned `allow`, the file reached the disk, and **7313 tests
passed**. The certifying test above stayed green because it uses the other
producer.

So a readiness blocker stopped being able to stop an effect, and nothing noticed.

This file asserts the same three things the kill-switch test asserts, because a
refusal is not one fact: the effect must not happen, the run must journal the
refusal under its own code, and the failure must reach the replan machinery. The
two controls are what make the first assertion mean something — without them,
"the file was not written" would also describe a run that never planned a write.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_write import FileWriteTool

_TARGET = "readiness_block_probe.txt"
#: The shape `readiness_blockers()` produces; the gate is `check_readiness`,
#: not the tuple, which is exactly what the second control below establishes.
_BLOCKERS = ("2 approval item(s) pending",)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _run(workspace: Path, *, blockers: tuple[str, ...],
         check_readiness: bool) -> tuple[Path, list[dict]]:
    registry = ToolRegistry()
    registry.register(FileWriteTool(workspace_root=workspace))
    trace_id = new_trace_id()
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=["answer"] * 4),
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs",
                           verbose=False),
        planner=FakePlanner(sources=[{
            "tool": "file_write",
            "arguments": {"path": _TARGET, "content": "written despite refusal"},
            "label": f"file_write:{_TARGET}",
            "expected_outcome": "writes the file",
        }]),
        # Approval says yes, so nothing but the gateway can stop the effect.
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
        clarification_enabled=False,
    )
    # No kill switch is engaged at any point in this file. The OTHER producer of
    # `block` is the whole subject here.
    agent.gateway_readiness_blockers = blockers
    agent.gateway_check_readiness = check_readiness

    agent.run("write the file")

    log_path = workspace / "logs" / f"{trace_id}.jsonl"
    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return workspace / _TARGET, events


def _outcomes(events: list[dict]) -> list[str]:
    return [e["payload"].get("outcome") for e in events
            if e["event"] == "gateway_decision"]


def _error_codes(events: list[dict]) -> list[str]:
    return [e["payload"].get("code") for e in events if e["event"] == "error"]


def test_a_readiness_blocker_prevents_the_write(workspace: Path) -> None:
    target, events = _run(workspace, blockers=_BLOCKERS, check_readiness=True)

    assert not target.exists(), (
        "a readiness blocker was engaged and the file was written anyway — the "
        "loop does not obey a block that came from this producer"
    )
    assert "block" in _outcomes(events), "the refusal must be journaled as a block"
    reasons = [
        r for e in events if e["event"] == "gateway_decision"
        for r in (e["payload"].get("reasons") or ())
    ]
    assert any("readiness_blocker" in r for r in reasons), (
        "the block must be attributed to the readiness producer BY NAME — a block "
        "attributed to the kill switch would leave this producer untested again"
    )
    codes = _error_codes(events)
    assert "gateway_blocked" in codes, (
        "a hard stop must surface under its own error code, not as a generic failure"
    )
    assert "replan_exhausted" in codes, (
        "the blocked step must reach the replan machinery — a refusal that leaves "
        "no trigger is a silent failure"
    )


def test_the_gate_is_check_readiness_and_not_the_blockers_alone(
    workspace: Path,
) -> None:
    """CONTROL: same blockers, gate off. Establishes what is actually deciding."""
    target, events = _run(workspace, blockers=_BLOCKERS, check_readiness=False)
    assert target.exists(), "with the gate off the write must go through"
    assert _outcomes(events) == ["allow"]


def test_an_unblocked_run_writes_the_file(workspace: Path) -> None:
    """CONTROL: nothing engaged. Without this, 'not written' proves nothing."""
    target, events = _run(workspace, blockers=(), check_readiness=False)
    assert target.exists()
    assert _outcomes(events) == ["allow"]
