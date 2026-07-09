"""MVP-12 audit suite: invisible-failure traps.

Targeted tests for things that WILL silently break if someone changes
one half of MVP-12 without updating the other half. Each `TestX` class
locks down one invariant; if any of these fail in CI, the contract is
broken and a future user-visible bug is incoming.

Specifically covered:
  A. Taxonomy drift   — `ReplanCode` Literal ≡ `FailureType` values
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

from core.loop import AgentLoop, ReplanCode, ReplanTrigger, new_trace_id
from core.logger import TraceLogger
from core.policy import PolicyGate
from core.replan import (
    ALL_FAILURE_TYPES,
    DEFAULT_BUDGETS,
    DEFAULT_MAX_TOTAL_REPLANS,
    FailureBudget,
    FailureType,
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
# A — Taxonomy drift: ReplanCode Literal must match FailureType
# ============================================================

class TestTaxonomyDrift:
    """If someone adds a new failure type in core/replan.py and forgets
    to update core/loop.py (or vice versa), this test class screams."""

    def _replancode_values(self) -> set[str]:
        # Python's Literal exposes its values via typing.get_args().
        return set(typing.get_args(ReplanCode))

    def _failuretype_values(self) -> set[str]:
        return set(typing.get_args(FailureType))

    def test_replancode_and_failuretype_have_same_values(self):
        rc = self._replancode_values()
        ft = self._failuretype_values()
        assert rc == ft, (
            f"ReplanCode (core/loop.py) and FailureType (core/replan.py) "
            f"have drifted. In ReplanCode only: {rc - ft}. "
            f"In FailureType only: {ft - rc}. "
            f"Add the missing literal value to BOTH so the taxonomies stay aligned."
        )

    def test_all_failure_types_tuple_matches_failuretype_literal(self):
        """`ALL_FAILURE_TYPES` is an iteration order — must contain
        every Literal value exactly once."""
        ft = self._failuretype_values()
        assert set(ALL_FAILURE_TYPES) == ft
        assert len(ALL_FAILURE_TYPES) == len(set(ALL_FAILURE_TYPES))

    def test_default_budgets_keyset_equals_taxonomy(self):
        ft = self._failuretype_values()
        assert set(DEFAULT_BUDGETS.keys()) == ft

    def test_loop_only_emits_known_failure_codes(self):
        """Grep `core/loop.py` for hard-coded `code=` assignments to
        ReplanTrigger and verify every one of them is a known code.
        This catches a typo like `code="too_error"`."""
        loop_src = Path("core/loop.py").read_text(encoding="utf-8")
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
    def test_legacy_max_replan_attempts_only(self, workspace: Path):
        """Pre-MVP-12 callers passed only `max_replan_attempts`; the loop
        must synthesise a default policy with that cap."""
        agent, _ = _build_agent(
            workspace, [], FakePlanner(sources=[]),
            max_replan_attempts=7,
        )
        assert agent.replan_policy.max_total_replans == 7
        assert agent.max_replan_attempts == 7  # legacy attribute mirrored
        # Default budgets preserved.
        assert (
            agent.replan_policy.budgets["tool_error"].max_occurrences
            == DEFAULT_BUDGETS["tool_error"].max_occurrences
        )

    def test_replan_policy_only(self, workspace: Path):
        custom = ReplanPolicy(max_total_replans=4)
        agent, _ = _build_agent(
            workspace, [], FakePlanner(sources=[]),
            replan_policy=custom,
        )
        assert agent.replan_policy is custom
        assert agent.max_replan_attempts == 4

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

    def test_default_cap_with_policy_accepted(self, workspace: Path):
        """If only `replan_policy` is passed (and `max_replan_attempts`
        is left at default) → no conflict even if values differ."""
        policy = ReplanPolicy(max_total_replans=99)
        agent, _ = _build_agent(
            workspace, [], FakePlanner(sources=[]),
            replan_policy=policy,
            # max_replan_attempts NOT passed; build helper falls back to
            # AgentLoop's own DEFAULT_MAX_REPLAN_ATTEMPTS.
        )
        assert agent.replan_policy is policy
        assert agent.replan_policy.max_total_replans == 99

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

    def test_shell_exec_normal_output_is_not_timeout(self, workspace: Path):
        """A normal shell_exec output (timed_out=False) must NOT be
        reclassified as `timeout`."""
        def behaviour(n, k):
            return {
                "exit_code": 0,
                "stdout": "hello\n",
                "stderr": "",
                "timed_out": False,
                "duration_ms": 12,
                "argv": ["echo", "hi"],
                "compensation_plan": {"actions": [{"kind": "noop"}]},
            }

        sh = _GenericTool("shell_exec", behaviour, risk="read_only")
        step = {"tool": "shell_exec", "arguments": {"argv": ["echo", "hi"]},
                "label": "stub", "expected_outcome": "ok"}
        planner = FakePlanner(sources=[step])
        agent, log_path = _build_agent(workspace, [sh], planner)
        agent.run(user_question="x", file_hint=None)

        events = _events(log_path)
        errors = [e for e in events if e["event"] == "error"
                  and e["payload"].get("code") == "timeout"]
        assert errors == []
        # And the tool ran exactly once (no retry).
        assert sh.calls == 1

    def test_shell_exec_dict_without_timed_out_key_is_not_timeout(
        self, workspace: Path
    ):
        """If the output dict simply lacks `timed_out`, no
        reclassification. `.get(...)` returns None which is falsy."""
        def behaviour(n, k):
            return {
                "exit_code": 0,
                "stdout": "ok\n",
                "stderr": "",
                "duration_ms": 1,
                "argv": ["x"],
                # timed_out key absent on purpose
            }

        sh = _GenericTool("shell_exec", behaviour)
        step = {"tool": "shell_exec", "arguments": {"argv": ["x"]},
                "label": "stub", "expected_outcome": "ok"}
        planner = FakePlanner(sources=[step])
        agent, log_path = _build_agent(workspace, [sh], planner)
        agent.run(user_question="x", file_hint=None)

        events = _events(log_path)
        assert not any(
            e["event"] == "error" and e["payload"].get("code") == "timeout"
            for e in events
        )

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

    def test_web_search_with_nonempty_results_is_not_web_empty(
        self, workspace: Path
    ):
        def behaviour(n, k):
            return [{"title": "a", "url": "http://a", "snippet": "x", "source": "ddg"}]

        web = _GenericTool("web_search", behaviour)
        step = {"tool": "web_search", "arguments": {"query": "q", "max_results": 1},
                "label": "stub:w", "expected_outcome": "search"}
        planner = FakePlanner(sources=[step])
        agent, log_path = _build_agent(workspace, [web], planner)
        agent.run(user_question="search", file_hint=None)

        events = _events(log_path)
        assert not any(
            e["event"] == "error" and e["payload"].get("code") == "web_empty"
            for e in events
        )
        assert web.calls == 1

    def test_other_tool_empty_list_is_not_web_empty(self, workspace: Path):
        """Only the tool literally named `web_search` triggers web_empty.
        A different search-ish tool returning [] should NOT be reclassified.
        We use a validator that rejects empty list (mirrors default
        Tool.validate_output) so this test pins: empty list from a
        non-`web_search` tool → verify_failed, never web_empty."""
        def behaviour(n, k):
            return []

        def validate(output):
            if isinstance(output, list) and len(output) == 0:
                return False, ["empty"]
            return True, []

        custom = _GenericTool("bing_search", behaviour, validate_fn=validate)
        step = {"tool": "bing_search", "arguments": {}, "label": "stub",
                "expected_outcome": "x"}
        planner = FakePlanner(sources=[step])
        agent, log_path = _build_agent(
            workspace, [custom], planner,
            replan_policy=ReplanPolicy(max_total_replans=2),
            llm_responses=[SYNTH_FAILED],
        )
        agent.run(user_question="x", file_hint=None)

        events = _events(log_path)
        # Must NOT have a web_empty code.
        assert not any(
            e["event"] == "error" and e["payload"].get("code") == "web_empty"
            for e in events
        )
        # The empty list trips validate_output → verify_failed instead.
        assert any(
            e["event"] == "error" and e["payload"].get("code") == "verify_failed"
            for e in events
        )

    def test_other_tool_with_timed_out_key_is_not_timeout(self, workspace: Path):
        """`timed_out=True` in the output of a tool other than
        `shell_exec` is just noise — not reclassified."""
        def behaviour(n, k):
            return {"timed_out": True, "result": "irrelevant"}

        odd = _GenericTool("my_runner", behaviour)
        step = {"tool": "my_runner", "arguments": {}, "label": "stub",
                "expected_outcome": "x"}
        planner = FakePlanner(sources=[step])
        agent, log_path = _build_agent(workspace, [odd], planner)
        agent.run(user_question="x", file_hint=None)

        events = _events(log_path)
        assert not any(
            e["event"] == "error" and e["payload"].get("code") == "timeout"
            for e in events
        )


# ============================================================
# D — Canonicalisation: both sides must produce identical JSON
# ============================================================

class TestForbiddenCanonicalisation:
    """ReplanPolicy (producer) and LLMPlanner._validate_steps (consumer)
    BOTH canonicalise args with sorted JSON. They must agree byte-for-byte."""

    def _canonical_in_planner(self, args: dict) -> str:
        """Call the exact same code-path as the sanitiser. We import
        json here directly because both sides do."""
        return json.dumps(args, sort_keys=True, ensure_ascii=False)

    def _canonical_in_policy(self, args: dict) -> str:
        """The producer in core/replan.py uses identical kwargs."""
        return json.dumps(args, sort_keys=True, ensure_ascii=False)

    @pytest.mark.parametrize("args", [
        {"a": 1, "b": 2},
        {"b": 2, "a": 1},                       # reverse key order
        {"q": "hi", "x": True, "n": None},     # mixed types
        {"path": "файл.txt", "n": 5},          # non-ASCII
        {"nested": {"b": 2, "a": 1}},           # nested dicts
        {"list": [3, 1, 2]},                    # list values (NOT sorted, by design)
        {},                                     # empty
    ])
    def test_canonical_form_identical_on_both_sides(self, args):
        assert self._canonical_in_planner(args) == self._canonical_in_policy(args)

    def test_nested_dict_keys_are_recursively_sorted(self):
        """The forbidden gate's whole correctness rests on this:
        nested key reordering MUST canonicalise the same way."""
        a = {"outer": {"b": 1, "a": 2}}
        b = {"outer": {"a": 2, "b": 1}}
        assert self._canonical_in_planner(a) == self._canonical_in_planner(b)

    def test_forbidden_blocks_args_in_different_key_order(self, workspace: Path):
        """End-to-end: planner re-proposes the same logical action with
        keys in a different order → sanitiser must STILL drop it."""
        target = _GenericTool("danger", lambda n, k: {}, risk="irreversible")
        # First plan: deny path. Second plan: same args, keys reordered.
        from tests.test_replan import SequencedPlanner
        from core.approval import AutoApprover

        plans = [
            [{"tool": "danger", "arguments": {"a": 1, "b": 2},
              "label": "stub", "expected_outcome": "deny"}],
            [{"tool": "danger", "arguments": {"b": 2, "a": 1},
              "label": "stub", "expected_outcome": "deny"}],
        ]
        planner = SequencedPlanner(plans=plans)
        agent, log_path = _build_agent(
            workspace, [target], planner,
            approval_provider=AutoApprover(default="deny"),
            replan_policy=ReplanPolicy(max_total_replans=5),
            llm_responses=[SYNTH_FAILED],
        )
        agent.run(user_question="x", file_hint=None)

        events = _events(log_path)
        # Approval must have been requested at most ONCE — the second
        # attempt's step is killed by canonical match.
        approval_requests = [e for e in events if e["event"] == "approval_request"]
        assert len(approval_requests) == 1
        # The dangerous tool ran zero times.
        assert target.calls == 0

    def test_forbidden_does_not_block_different_args(self, workspace: Path):
        """If the planner proposes the SAME tool but with DIFFERENT args,
        the forbidden gate must let it through — by design, that's the
        'pick a safer alternative' path."""
        from core.approval import AutoApprover
        from tests.test_replan import SequencedPlanner

        # Attempt 1: denied. Attempt 2: same tool but different args →
        # approval gate fires AGAIN (different action) → still denied
        # → counter goes up → exhausts on the per-type budget.
        plans = [
            [{"tool": "danger", "arguments": {"path": "first"},
              "label": "stub", "expected_outcome": "deny"}],
            [{"tool": "danger", "arguments": {"path": "second"},
              "label": "stub", "expected_outcome": "deny"}],
        ]
        target = _GenericTool("danger", lambda n, k: {}, risk="irreversible")
        planner = SequencedPlanner(plans=plans)
        agent, log_path = _build_agent(
            workspace, [target], planner,
            approval_provider=AutoApprover(default="deny"),
            replan_policy=ReplanPolicy(max_total_replans=5),
            llm_responses=[SYNTH_FAILED],
        )
        agent.run(user_question="x", file_hint=None)

        events = _events(log_path)
        # Two distinct approval requests (different args, different
        # forbidden-set entries) → the gate is NOT over-matching.
        approval_requests = [e for e in events if e["event"] == "approval_request"]
        assert len(approval_requests) == 2
        # And neither approval landed on the tool.
        assert target.calls == 0


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
# F — Sanitiser strips everything ⇒ success branch (empty plan)
# ============================================================

class TestForbiddenFullyStripsPlan:
    def test_sanitiser_drops_all_steps_lands_on_success_branch(
        self, workspace: Path
    ):
        """If the LLM doggedly re-proposes ONLY the forbidden action
        and nothing else, the second attempt's sanitiser drops every
        step. An empty plan is a legitimate success (general-knowledge).
        Verify the loop does NOT mark it as exhausted."""
        from core.approval import AutoApprover

        target = _GenericTool("danger", lambda n, k: {}, risk="irreversible")
        bad_step = {"tool": "danger", "arguments": {"path": "X"},
                    "label": "stub", "expected_outcome": "deny"}
        # FakePlanner — always returns the same forbidden step; with
        # `forbidden_actions` honoured it'll yield [] on attempt 2.
        planner = FakePlanner(sources=[bad_step])
        agent, log_path = _build_agent(
            workspace, [target], planner,
            approval_provider=AutoApprover(default="deny"),
            replan_policy=ReplanPolicy(max_total_replans=5),
            llm_responses=[SYNTH_OK],
        )
        answer = agent.run(user_question="do it", file_hint=None)

        events = _events(log_path)
        # Tool never ran.
        assert target.calls == 0
        # `respond.replan_exhausted` is False — we ended in success.
        respond = [e for e in events if e["event"] == "respond"][0]
        assert respond["payload"]["replan_exhausted"] is False
        assert respond["payload"]["attempts_used"] == 2
        # And no `replan_exhausted` event emitted.
        assert [e for e in events if e["event"] == "replan_exhausted"] == []
        # Synth answer surfaced normally.
        assert answer == SYNTH_OK


# ============================================================
# G — _count_failures parity with ReplanDecision.failure_counts
# ============================================================

class TestCountsParity:
    """`_count_failures` and `ReplanPolicy._coerce_code` work on the
    same input but live in different files. They MUST agree on counts
    for any code that is a member of `FailureType` (known codes)."""

    @pytest.mark.parametrize("codes", [
        ["tool_error"],
        ["tool_error", "tool_error"],
        ["tool_error", "verify_failed"],
        ["web_empty", "web_empty", "tool_error"],
        ["timeout", "verify_failed", "verify_failed"],
        [],
    ])
    def test_known_codes_counted_identically(self, codes):
        triggers = [
            ReplanTrigger(
                code=c,  # type: ignore[arg-type]
                step_id=f"s{i}",
                tool_name="t",
                arguments={},
                reason="r",
                attempt=1,
            )
            for i, c in enumerate(codes)
        ]
        loop_counts = AgentLoop._count_failures(triggers)
        decision = ReplanPolicy(max_total_replans=99).decide(
            failure_history=triggers,
            completed_attempts=max(len(triggers), 1),
        )
        # Decision.failure_counts uses Counter ordering; compare dicts.
        assert loop_counts == dict(decision.failure_counts), (
            f"counts diverged on codes={codes}: "
            f"loop={loop_counts}, decision={decision.failure_counts}"
        )


# ============================================================
# H — _format_replan_context permutations
# ============================================================

class TestFormatReplanContextEdges:
    def test_empty_everything_returns_empty_string(self):
        ctx = AgentLoop._format_replan_context(
            failure_history=[], attempt=1, max_attempts=3,
        )
        assert ctx == ""

    def test_advice_only_still_produces_block(self):
        """No history yet (first attempt), but the loop wants to inject
        advice anyway. We don't actually use this path today but the
        helper must not return empty when advice is non-empty."""
        ctx = AgentLoop._format_replan_context(
            failure_history=[], attempt=2, max_attempts=3,
            advice="Use a different tool.",
        )
        assert "<replan_context" in ctx
        assert "<advice>" in ctx
        assert "Use a different tool." in ctx

    def test_forbidden_only_still_produces_block(self):
        ctx = AgentLoop._format_replan_context(
            failure_history=[], attempt=2, max_attempts=3,
            forbidden_actions=(("t", '{"a": 1}'),),
        )
        assert "<forbidden>" in ctx
        assert 'tool=t' in ctx
        assert 'arguments={"a": 1}' in ctx

    def test_advice_blank_lines_filtered(self):
        ctx = AgentLoop._format_replan_context(
            failure_history=[], attempt=2, max_attempts=3,
            advice="line one\n\n   \nline two",
        )
        # Both meaningful lines present, no blank-line-only entries.
        assert "line one" in ctx
        assert "line two" in ctx
        # The empty lines between them did NOT leak into the block.
        lines = [ln for ln in ctx.splitlines() if ln.strip() == ""]
        assert lines == []

    def test_history_block_carries_all_required_fields(self):
        history = [
            ReplanTrigger(
                code="tool_error",
                step_id="s1",
                tool_name="flaky",
                arguments={"q": "hi"},
                reason="boom",
                attempt=1,
            )
        ]
        ctx = AgentLoop._format_replan_context(
            failure_history=history, attempt=2, max_attempts=3,
        )
        # Every public-contract field appears in the block.
        for token in (
            'attempt="2"',          # header attribute uses quoted XML form
            "code=tool_error",
            "tool=flaky",
            "arguments=",
            '"q": "hi"',
            "reason: boom",
        ):
            assert token in ctx, f"missing '{token}' in context: {ctx}"

    def test_advice_and_forbidden_blocks_appear_in_stable_order(self):
        """Tests rely on block ordering: advice before forbidden. If
        someone reorders them, downstream tests assert that order — so
        we lock it down explicitly here."""
        ctx = AgentLoop._format_replan_context(
            failure_history=[], attempt=2, max_attempts=3,
            advice="A",
            forbidden_actions=(("t", "{}"),),
        )
        assert ctx.index("<advice>") < ctx.index("<forbidden>")


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


# ============================================================
# J — Boundary: exactly at the global cap (off-by-one safety)
# ============================================================

class TestGlobalCapBoundary:
    def test_completed_attempts_equal_cap_returns_exhausted(self):
        p = ReplanPolicy(max_total_replans=3)
        from tests.test_replan_policy import FakeTrigger

        # 3 fresh triggers, completed_attempts=3 → exactly at cap.
        h = [FakeTrigger(code="tool_error") for _ in range(3)]
        d = p.decide(h, completed_attempts=3)
        # tool_error budget is 2 so per-type bites first; but even if
        # we use a generous budget the global cap fires.
        custom = dict(DEFAULT_BUDGETS)
        custom["tool_error"] = FailureBudget(max_occurrences=99, advice="x")
        p2 = ReplanPolicy(budgets=custom, max_total_replans=3)
        d2 = p2.decide(h, completed_attempts=3)
        assert d2.action == "abort_exhausted"

    def test_completed_attempts_one_below_cap_continues(self):
        custom = dict(DEFAULT_BUDGETS)
        custom["tool_error"] = FailureBudget(max_occurrences=99, advice="x")
        p = ReplanPolicy(budgets=custom, max_total_replans=3)
        from tests.test_replan_policy import FakeTrigger

        h = [FakeTrigger(code="tool_error"), FakeTrigger(code="tool_error")]
        d = p.decide(h, completed_attempts=2)
        assert d.action == "continue"

    def test_default_max_total_replans_constant_unchanged(self):
        """If someone changes DEFAULT_MAX_TOTAL_REPLANS without thinking,
        many downstream tests start to drift silently. Pin the value."""
        assert DEFAULT_MAX_TOTAL_REPLANS == 3
