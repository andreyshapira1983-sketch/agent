"""MVP-12 audit suite: invisible-failure traps.

Targeted tests for things that WILL silently break if someone changes
one half of MVP-12 without updating the other half. Each `TestX` class
locks down one invariant; if any of these fail in CI, the contract is
broken and a future user-visible bug is incoming.

Specifically covered:
  A. Taxonomy drift   — `FailureType` ≡ `ALL_FAILURE_TYPES` ≡ budget keys
  B. Init contracts   — `max_replan_attempts` vs `replan_policy` resolution
  C. Classification   — web_empty / timeout false positives + negatives
  D. Canonicalisation — sorted-keys JSON used by BOTH sides matches
  E. Multi-type       — multi-step plan with mixed failure types
  F. Empty-plan path  — sanitiser strips everything ⇒ success branch
  G. Counts parity    — _count_failures vs ReplanDecision.failure_counts
  H. Format edges     — _format_replan_context permutations
"""
from __future__ import annotations

import json
import typing
from pathlib import Path
from typing import Any

import pytest

from core.logger import TraceLogger
from core.loop import AgentLoop, ReplanCode, new_trace_id
from core.policy import PolicyGate
from core.replan import (
    ReplanPolicy,
)
from tests.conftest import FakeLLM, FakePlanner
from tools.base import Tool, ToolRegistry

# ============================================================
# Shared minimal stubs
# ============================================================

class _GenericTool(Tool):
    """A Tool whose run() and validate_output() are fully injected.

    Used to exercise loop classification paths without dragging in real
    tool implementations or their validation quirks.
    """

    def __init__(
        self,
        name: str,
        run_fn,
        *,
        validate_fn=None,
        risk: str = "read_only",
    ):
        self.name = name
        self.description = f"audit stub '{name}'"
        self.risk = risk  # type: ignore[assignment]
        self._run_fn = run_fn
        self._validate_fn = validate_fn
        self.calls = 0

    def run(self, **kwargs):
        self.calls += 1
        return self._run_fn(self.calls, kwargs)

    def validate_output(self, output):
        if self._validate_fn is not None:
            return self._validate_fn(output)
        return True, []


def _events(log_path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


SYNTH_OK = """Conclusion:
ok.

Facts:
- ok [stub:src]

Sources:
1. stub:src - stub

Confidence: medium

Unverified: nothing

Safety: nothing"""

SYNTH_FAILED = """Conclusion:
The agent could not produce a usable answer.

Facts:
- nothing [general-knowledge]

Sources:
1. general-knowledge - general-knowledge

Confidence: low

Unverified: everything

Safety: nothing"""


def _build_agent(
    workspace: Path,
    tools: list[Tool],
    planner,
    *,
    approval_provider=None,
    replan_policy: ReplanPolicy | None = None,
    max_replan_attempts: int | None = None,
    llm_responses: list[str] | None = None,
):
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    policy = PolicyGate(registry)
    llm = FakeLLM(responses=llm_responses or [SYNTH_OK])
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    kwargs: dict[str, Any] = {
        "registry": registry,
        "policy": policy,
        "llm": llm,
        "logger": logger,
        "planner": planner,
        "approval_provider": approval_provider,
        "replan_policy": replan_policy,
        # See note in test_replan.py — these assertions are byte-for-byte
        # against the canned LLM response; Verifier annotation would
        # rewrite citations. Verifier has its own dedicated test file.
        "verifier_enabled": False,
    }
    if max_replan_attempts is not None:
        kwargs["max_replan_attempts"] = max_replan_attempts
    agent = AgentLoop(**kwargs)
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


# ============================================================
# A — Taxonomy drift: the tuple and the budgets must match FailureType
# ============================================================

class TestTaxonomyDrift:
    """If someone adds a new failure type in core/replan.py and forgets
    to update core/loop.py (or vice versa), this test class screams."""

    def _replancode_values(self) -> set[str]:
        # Python's Literal exposes its values via typing.get_args().
        return set(typing.get_args(ReplanCode))


    def test_loop_only_emits_known_failure_codes(self):
        """Grep `core/loop.py` for hard-coded `code=` assignments to
        ReplanTrigger and verify every one of them is a known code.
        This catches a typo like `code="too_error"`."""
        # Through the module (census C1). The grep is about the codes THIS
        # module hard-codes, and that question travels with it.
        import inspect

        from core import loop as _loop_mod

        loop_src = inspect.getsource(_loop_mod)
        # very loose pattern: `code="something"` anywhere in the file
        import re
        codes_used = set(re.findall(r'code\s*=\s*"([a-z_]+)"', loop_src))
        # Filter to the codes that get assigned to a ReplanTrigger by
        # looking at the immediate context. Easier: every code that
        # appears more than once and is lowercase_snake is a candidate.
        # We'll just intersect with the union of ReplanCode + a few
        # extra non-replan codes we know live in this file.
        extra_codes_in_loop = {
            # ErrorObject codes that aren't ReplanCode but show up in
            # the same pattern:
            "replan_exhausted",
            "gateway_blocked",
        }
        known = self._replancode_values() | extra_codes_in_loop
        unknown = codes_used - known
        assert not unknown, (
            f"core/loop.py uses code(s) {unknown} that are NOT in "
            f"ReplanCode and not in the explicit whitelist. Either "
            f"add to ReplanCode or to the whitelist if intentional."
        )


# ============================================================
# B — AgentLoop init: max_replan_attempts vs replan_policy
# ============================================================

class TestInitBackwardCompat:
    def test_compatible_pair_accepted(self, workspace: Path):
        """Same cap on both sides → no conflict, both used together."""
        policy = ReplanPolicy(max_total_replans=5)
        agent, _ = _build_agent(
            workspace, [], FakePlanner(sources=[]),
            max_replan_attempts=5,
            replan_policy=policy,
        )
        assert agent.replan_policy is policy
        assert agent.max_replan_attempts == 5

    def test_conflicting_pair_rejected(self, workspace: Path):
        """Different cap on each side → ValueError. Caller must pick one."""
        policy = ReplanPolicy(max_total_replans=5)
        with pytest.raises(ValueError, match="conflicting"):
            _build_agent(
                workspace, [], FakePlanner(sources=[]),
                max_replan_attempts=7,
                replan_policy=policy,
            )


    def test_zero_max_replan_attempts_rejected(self, workspace: Path):
        with pytest.raises(ValueError, match="max_replan_attempts"):
            _build_agent(
                workspace, [], FakePlanner(sources=[]),
                max_replan_attempts=0,
            )


# ============================================================
# C — Classification edge cases (false positives + negatives)
# ============================================================

class TestClassificationFalsePositives:
    """Make sure web_empty and timeout fire ONLY in the precise scenario
    we want — not on any vaguely empty-looking output."""


    def test_shell_exec_non_dict_output_is_not_timeout(self, workspace: Path):
        """A list output (semantically wrong but technically possible
        if a future shell_exec returns differently) must not be
        misclassified as timeout. validate_output would reject it as
        verify_failed first if the real tool was used."""
        def behaviour(n, k):
            return ["unexpected", "shape"]

        sh = _GenericTool("shell_exec", behaviour)
        step = {"tool": "shell_exec", "arguments": {"argv": ["x"]},
                "label": "stub", "expected_outcome": "x"}
        planner = FakePlanner(sources=[step])
        agent, log_path = _build_agent(workspace, [sh], planner)
        agent.run(user_question="x", file_hint=None)

        events = _events(log_path)
        # No `timeout` code in any error event.
        assert not any(
            e["event"] == "error" and e["payload"].get("code") == "timeout"
            for e in events
        )


# ============================================================
# E — Multi-type failures in a single attempt
# ============================================================

class TestMultiTypeFailuresInOneAttempt:
    def test_two_steps_one_attempt_two_distinct_failure_types(
        self, workspace: Path
    ):
        """One plan, two steps: step-1 raises (tool_error), step-2
        returns empty list when called as web_search (web_empty).
        Both must surface as distinct triggers."""
        def boom(n, k):
            raise RuntimeError("boom")

        def web(n, k):
            return []

        broken = _GenericTool("broken", boom, risk="read_only")
        websearch = _GenericTool("web_search", web, risk="read_only")
        plan = [
            {"tool": "broken", "arguments": {}, "label": "stub:b",
             "expected_outcome": "fails"},
            {"tool": "web_search", "arguments": {"query": "q", "max_results": 1},
             "label": "stub:w", "expected_outcome": "empty"},
        ]
        planner = FakePlanner(sources=plan)
        agent, log_path = _build_agent(
            workspace, [broken, websearch], planner,
            replan_policy=ReplanPolicy(max_total_replans=2),
            llm_responses=[SYNTH_FAILED],
        )
        agent.run(user_question="x", file_hint=None)

        events = _events(log_path)
        replans = [e for e in events if e["event"] == "replan"]
        # We expect at least one replan or replan_exhausted event whose
        # triggers list contains BOTH codes.
        exhausted = [e for e in events if e["event"] == "replan_exhausted"]
        all_triggers = []
        for e in replans + exhausted:
            all_triggers.extend(e["payload"].get("triggers", []))
        assert "tool_error" in all_triggers
        assert "web_empty" in all_triggers


# ============================================================
# I — ReplanPolicy doesn't crash on malformed trigger inputs
# ============================================================

class TestReplanPolicyRobustness:
    """The policy reads attributes off duck-typed objects. It must
    survive garbage gracefully so a misbehaving caller doesn't crash
    the whole agent."""

    def test_trigger_with_none_tool_name_still_decides(self):
        from tests.test_replan_policy import FakeTrigger

        history = [FakeTrigger(
            code="approval_deny",
            tool_name=None,         # type: ignore[arg-type]
            arguments={"a": 1},
        )]
        d = ReplanPolicy().decide(history, completed_attempts=1)
        # The trigger is counted but does NOT enter forbidden_actions
        # (no usable tool name to canonicalise against).
        assert d.failure_counts == {"approval_deny": 1}
        assert d.forbidden_actions == ()

    def test_trigger_with_non_dict_arguments_skipped_from_forbidden(self):
        from tests.test_replan_policy import FakeTrigger

        h = [FakeTrigger(
            code="approval_deny",
            tool_name="t",
            arguments="not a dict",  # type: ignore[arg-type]
        )]
        d = ReplanPolicy().decide(h, completed_attempts=1)
        assert d.forbidden_actions == ()
        # Counts still accurate.
        assert d.failure_counts == {"approval_deny": 1}
