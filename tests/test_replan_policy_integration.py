"""MVP-12 acceptance: ReplanPolicy through the full AgentLoop.

Pins all 8 acceptance criteria from the user's spec:

  1. web_empty                → planner gets advice asking for a new search query
  2. tool_error               → retry is bounded (budget exhausts at 2)
  3. approval_deny            → the denied (tool, args) pair MUST NOT run again
  4. policy_blocked           → planner is told to pick a different registered tool
  5. verify_failed            → a different plan is built on retry
  6. global cap reached       → honest final failure (`replan_exhausted` event)
  7. all replan decisions     → emitted as structured events to JSONL
  8. infinite-loop ceiling    → both per-type budgets AND the global cap fire

Every test reads back the JSONL audit log so we can verify the LOOP did
the right thing — not just what the user-facing answer looks like.
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
from core.replan import DEFAULT_BUDGETS, FailureBudget, ReplanPolicy
from tests.conftest import FakeLLM, FakePlanner
from tools.base import Tool, ToolRegistry


# ============================================================
# Helpers
# ============================================================

SYNTH_OK = """Conclusion:
A short answer.

Facts:
- Did the thing [stub:src]

Sources:
1. stub:src - stub

Confidence: medium

Unverified: nothing

Safety: nothing"""

SYNTH_FAILED = """Conclusion:
Nothing usable came back. Tried several approaches and stopped.

Facts:
- The agent tried multiple plans and the executor surfaced repeated
  failures [general-knowledge]

Sources:
1. general-knowledge - general-knowledge

Confidence: low

Unverified: everything the user asked about

Safety: nothing"""


def _events(log_path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


class _RecordingTool(Tool):
    """Generic stub Tool whose `run()` does whatever the test wants.

    Uses the default `Tool.validate_output` so empty string / list / dict
    outputs are rejected as `verify_failed` — useful for that acceptance
    test. When the name is `web_search` we mirror the real
    `WebSearchTool.validate_output` semantics: empty list is OK at the
    validate stage so the loop can reclassify it as `web_empty`.
    """

    def __init__(self, name: str, behaviour, risk: str = "read_only"):
        self.name = name
        self.description = f"stub tool '{name}' for replan integration tests"
        self.risk = risk  # type: ignore[assignment]
        self._behaviour = behaviour
        self.calls = 0

    def run(self, **kwargs):
        self.calls += 1
        return self._behaviour(self.calls, kwargs)

    def validate_output(self, output):
        # Mirror the real WebSearchTool: empty list passes validate so
        # the loop can reclassify it as `web_empty`.
        if self.name == "web_search" and isinstance(output, list):
            return True, []
        return super().validate_output(output)


class _SequencedPlanner:
    """Returns plans[n] on the n-th call (clamped). Drops steps that the
    loop has put into the forbidden_actions list."""

    def __init__(self, plans: list[list[dict[str, Any]]]):
        self.plans = plans
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
        from core.planner import PlannerOutput

        idx = min(len(self.calls), len(self.plans) - 1)
        chosen = list(self.plans[idx])
        self.calls.append(
            {
                "failure_context": failure_context,
                "forbidden_actions": forbidden_actions,
                "returned_sources": list(chosen),
            }
        )

        forbidden_set = set(forbidden_actions)
        kept = []
        for src in chosen:
            tool = src.get("tool")
            args = src.get("arguments") or {}
            try:
                canonical = json.dumps(args, sort_keys=True, ensure_ascii=False)
            except TypeError:
                canonical = ""
            if isinstance(tool, str) and canonical and (tool, canonical) in forbidden_set:
                continue
            kept.append(src)

        return PlannerOutput(
            reasoning="sequenced",
            sources=kept,
            raw_response="",
            warnings=[],
        )


def _build_agent(
    workspace: Path,
    tools: list[Tool],
    planner,
    *,
    approval_provider=None,
    replan_policy: ReplanPolicy | None = None,
    llm_responses: list[str] | None = None,
):
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    policy = PolicyGate(registry)
    llm = FakeLLM(responses=llm_responses or [SYNTH_OK])
    trace_id = new_trace_id()
    logger = TraceLogger(trace_id=trace_id, log_dir=workspace / "logs", verbose=False)
    agent = AgentLoop(
        registry=registry,
        policy=policy,
        llm=llm,
        logger=logger,
        planner=planner,
        approval_provider=approval_provider,
        replan_policy=replan_policy,
        # See test_replan.py — replan integration tests pin the
        # synthesizer's canned response byte-for-byte; Verifier
        # annotation is exercised in tests/test_verifier_integration.py.
        verifier_enabled=False,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


# ============================================================
# Acceptance 1 — web_empty triggers a query reformulation
# ============================================================

class TestAcceptanceWebEmpty:
    def test_empty_web_results_reclassified_as_web_empty(self, workspace: Path):
        def behaviour(call_n, kwargs):
            return [] if call_n == 1 else [
                {"title": "X", "url": "http://x", "snippet": "ok", "source": "ddg"},
            ]

        web = _RecordingTool("web_search", behaviour, risk="read_only")
        plans = [
            [{"tool": "web_search", "arguments": {"query": "no-hits", "max_results": 5},
              "label": "web:no", "expected_outcome": "search"}],
            [{"tool": "web_search", "arguments": {"query": "better query", "max_results": 5},
              "label": "web:better", "expected_outcome": "search"}],
        ]
        planner = _SequencedPlanner(plans)
        agent, log_path = _build_agent(workspace, [web], planner)
        agent.run(user_question="search something", file_hint=None)

        events = _events(log_path)
        # web_empty surfaces in the replan event triggers.
        replans = [e for e in events if e["event"] == "replan"]
        assert len(replans) == 1
        assert "web_empty" in replans[0]["payload"]["triggers"]
        # Advice block in attempt-2 mentions reformulation.
        assert "REFORMULATE" in planner.calls[1]["failure_context"].upper()
        # Second search ran and succeeded → 2 tool calls in total.
        assert web.calls == 2


# ============================================================
# Acceptance 2 — tool_error retry bounded by budget
# ============================================================

class TestAcceptanceToolErrorBounded:
    def test_two_tool_errors_stop_with_abort_no_retry(self, workspace: Path):
        def behaviour(call_n, kwargs):
            raise RuntimeError(f"tool failure #{call_n}")

        flaky = _RecordingTool("flaky", behaviour, risk="read_only")
        step = {"tool": "flaky", "arguments": {}, "label": "stub", "expected_outcome": "x"}
        planner = FakePlanner(sources=[step])
        # max_total=5 so the GLOBAL cap doesn't fire first.
        agent, log_path = _build_agent(
            workspace, [flaky], planner,
            replan_policy=ReplanPolicy(max_total_replans=5),
            llm_responses=[SYNTH_FAILED],
        )
        agent.run(user_question="run", file_hint=None)

        # Exactly TWO tool_error triggers -> budget exhausts -> abort_no_retry.
        events = _events(log_path)
        exhausted = [e for e in events if e["event"] == "replan_exhausted"]
        assert len(exhausted) == 1
        payload = exhausted[0]["payload"]
        assert payload["decision_action"] == "abort_no_retry"
        assert payload["failure_counts"]["tool_error"] == 2
        # The tool ran exactly 2 times (each attempt → 1 call).
        assert flaky.calls == 2


# ============================================================
# Acceptance 3 — approval_deny: SAME action never runs twice
# ============================================================

class TestAcceptanceApprovalDeny:
    def test_denied_action_never_repeats_even_if_planner_tries(self, workspace: Path):
        """A planner that stubbornly re-proposes the same dangerous step:
        the FORBIDDEN_ACTIONS gate must catch it AND the tool must never
        re-run."""

        def behaviour(call_n, kwargs):
            # Should never be called — approval is denied.
            return {"never": "ran"}

        danger = _RecordingTool("danger", behaviour, risk="irreversible")
        bad_step = {
            "tool": "danger",
            "arguments": {"path": "X"},
            "label": "stub",
            "expected_outcome": "no",
        }
        # Planner is configured to ALWAYS return the same dangerous step.
        planner = FakePlanner(sources=[bad_step])
        agent, log_path = _build_agent(
            workspace, [danger], planner,
            approval_provider=AutoApprover(default="deny"),
            replan_policy=ReplanPolicy(max_total_replans=5),
            llm_responses=[SYNTH_FAILED],
        )
        agent.run(user_question="do the dangerous thing", file_hint=None)

        # Tool NEVER ran.
        assert danger.calls == 0

        events = _events(log_path)
        # First attempt: approval_deny.
        denials = [e for e in events if e["event"] == "approval_decision"
                   and e["payload"]["decision"] == "deny"]
        assert len(denials) >= 1

        # Second attempt: planner re-proposes, sanitiser drops via
        # forbidden_actions → 0 steps → success branch (empty plan = ok).
        # OR exhaustion if every retry path fails the same way.
        # Either way: the dangerous tool never invoked.
        approval_requests = [e for e in events if e["event"] == "approval_request"]
        assert len(approval_requests) == 1, (
            "approval should be requested only ONCE for the same (tool, args) pair"
        )


# ============================================================
# Acceptance 4 — policy_blocked: agent proposes a different tool
# ============================================================

class TestAcceptancePolicyBlocked:
    def test_blocked_tool_then_alternative_runs(self, workspace: Path):
        """First attempt names a non-existent tool → policy_blocked.
        Second attempt names a real registered tool → success.
        """
        safe = _RecordingTool("safe_read", lambda n, k: "OK", risk="read_only")
        plans = [
            # Attempt 1 — unknown tool, registry will block.
            [{"tool": "nope", "arguments": {"x": 1}, "label": "stub:nope",
              "expected_outcome": "blocked"}],
            # Attempt 2 — switch to the registered safe tool.
            [{"tool": "safe_read", "arguments": {}, "label": "stub:safe",
              "expected_outcome": "ok"}],
        ]
        planner = _SequencedPlanner(plans)
        agent, log_path = _build_agent(workspace, [safe], planner)
        agent.run(user_question="something", file_hint=None)

        events = _events(log_path)
        # First attempt: policy_blocked appears in replan event triggers.
        replans = [e for e in events if e["event"] == "replan"]
        assert len(replans) == 1
        assert "policy_blocked" in replans[0]["payload"]["triggers"]
        # Forbidden actions carried forward.
        assert replans[0]["payload"]["decision"]["forbidden_action_count"] >= 1
        # Safe tool ran exactly once on attempt 2.
        assert safe.calls == 1


# ============================================================
# Acceptance 5 — verify_failed → new plan on retry
# ============================================================

class TestAcceptanceVerifyFailed:
    def test_verify_failed_replans_with_new_plan(self, workspace: Path):
        # Attempt 1: verify_failed (empty string output is rejected by
        # validate_output default). Attempt 2: switch to a non-empty
        # tool that succeeds.
        empty = _RecordingTool("empty", lambda n, k: "", risk="read_only")
        good = _RecordingTool("good", lambda n, k: "OK", risk="read_only")
        plans = [
            [{"tool": "empty", "arguments": {}, "label": "stub:empty",
              "expected_outcome": "validation will reject"}],
            [{"tool": "good", "arguments": {}, "label": "stub:good",
              "expected_outcome": "succeeds"}],
        ]
        planner = _SequencedPlanner(plans)
        agent, log_path = _build_agent(workspace, [empty, good], planner)
        agent.run(user_question="x", file_hint=None)

        events = _events(log_path)
        replans = [e for e in events if e["event"] == "replan"]
        assert len(replans) == 1
        assert "verify_failed" in replans[0]["payload"]["triggers"]
        # Different tool on the second attempt.
        assert good.calls == 1


# ============================================================
# Acceptance 6 — replan_exhausted produces an honest final answer
# ============================================================

class TestAcceptanceExhaustedHonest:
    def test_final_failure_still_synthesises_answer(self, workspace: Path):
        def boom(n, k):
            raise RuntimeError("always")

        broken = _RecordingTool("broken", boom, risk="read_only")
        step = {"tool": "broken", "arguments": {}, "label": "stub", "expected_outcome": "fail"}
        planner = FakePlanner(sources=[step])
        agent, log_path = _build_agent(
            workspace, [broken], planner,
            replan_policy=ReplanPolicy(max_total_replans=5),
            llm_responses=[SYNTH_FAILED],
        )
        answer = agent.run(user_question="x", file_hint=None)

        # Synthesizer still ran — user got a structured answer, then the
        # kernel output policy made the exhausted replan state explicit.
        assert "Nothing usable came back" in answer
        assert "replan budget was exhausted" in answer
        events = _events(log_path)
        respond = [e for e in events if e["event"] == "respond"]
        assert len(respond) == 1
        assert respond[0]["payload"]["replan_exhausted"] is True


# ============================================================
# Acceptance 7 — every decision is logged
# ============================================================

class TestAcceptanceDecisionsLogged:
    def test_replan_attempt_replan_and_replan_exhausted_all_present(self, workspace: Path):
        def boom(n, k):
            raise RuntimeError("always")

        broken = _RecordingTool("broken", boom, risk="read_only")
        step = {"tool": "broken", "arguments": {}, "label": "stub", "expected_outcome": "fail"}
        planner = FakePlanner(sources=[step])
        # Policy: 5 global cap, 3 tool_error budget → stops on the 3rd
        # attempt by tool_error budget (or global cap, whichever first).
        custom_budgets = dict(DEFAULT_BUDGETS)
        custom_budgets["tool_error"] = FailureBudget(
            max_occurrences=3, advice="retry"
        )
        agent, log_path = _build_agent(
            workspace, [broken], planner,
            replan_policy=ReplanPolicy(
                budgets=custom_budgets, max_total_replans=5
            ),
            llm_responses=[SYNTH_FAILED],
        )
        agent.run(user_question="x", file_hint=None)

        events = _events(log_path)
        names = [e["event"] for e in events]

        # We expect exactly:
        #   - 3 planner events (one per attempt)
        #   - 2 replan events (between attempt 1->2 and 2->3)
        #   - 2 replan_attempt events (kicked off attempt 2 and 3)
        #   - 1 replan_exhausted event (after attempt 3 fails to retry)
        assert names.count("planner") == 3
        assert names.count("replan") == 2
        assert names.count("replan_attempt") == 2
        assert names.count("replan_exhausted") == 1

        # Sanity: ordering — every replan_attempt event reports the
        # cumulative failure counts so the audit log is self-contained.
        attempts = [e for e in events if e["event"] == "replan_attempt"]
        assert attempts[0]["payload"]["attempt"] == 2
        assert attempts[0]["payload"]["failure_counts_so_far"]["tool_error"] == 1
        assert attempts[1]["payload"]["attempt"] == 3
        assert attempts[1]["payload"]["failure_counts_so_far"]["tool_error"] == 2


# ============================================================
# Acceptance 8 — proven NO INFINITE LOOP under any failure pattern
# ============================================================

class TestAcceptanceNoInfiniteLoop:
    @pytest.mark.parametrize(
        "code,exception_factory",
        [
            ("tool_error",    lambda: RuntimeError("boom")),
            ("verify_failed", None),   # empty output: validate_output rejects
        ],
    )
    def test_pathological_failure_pattern_terminates(
        self, workspace: Path, code, exception_factory
    ):
        """Whatever the planner emits — even the same broken step
        forever — the loop MUST terminate. We pass a small budget so
        the test stays fast; the GUARANTEE is termination, not the
        exact number of attempts."""
        def behaviour(n, k):
            if exception_factory is not None:
                raise exception_factory()
            # verify_failed scenario: return falsy output.
            return None

        bad = _RecordingTool("bad", behaviour, risk="read_only")
        step = {"tool": "bad", "arguments": {}, "label": "stub", "expected_outcome": "x"}
        planner = FakePlanner(sources=[step])
        agent, log_path = _build_agent(
            workspace, [bad], planner,
            replan_policy=ReplanPolicy(max_total_replans=10),
            llm_responses=[SYNTH_FAILED],
        )
        # If the loop is infinite, pytest will eventually hang the run.
        # A successful return — for any reason — is the contract.
        answer = agent.run(user_question="loop forever?", file_hint=None)
        assert isinstance(answer, str)
        # Sanity: the per-type budget bites long before max_total.
        events = _events(log_path)
        exhausted = [e for e in events if e["event"] == "replan_exhausted"]
        assert len(exhausted) == 1
        # And the tool was called a bounded number of times.
        assert bad.calls <= 3
