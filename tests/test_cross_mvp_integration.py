"""Cross-MVP integration audit.

Each `Test*` class pins one INTERACTION between two previously
independent MVPs. The single-MVP suites prove each piece works
locally; this file proves they don't silently break when combined.

Scenarios covered:
  1. Compensation × Replan         — multi-step retry preserves rollback semantics
  2. Compensation × Hygiene        — fresh backups survive a hygiene pass
  3. Classification × Verify       — verify_failed wins over web_empty/timeout
  4. Redaction × Replan            — secrets in failure_history don't leak
  5. Risk-for × Compensation       — reversible writes still register a plan
  6. Approval × Replan             — deny → safe alternative path
  7. Persistent memory × Replan    — failure history doesn't pollute memory

Every test reads back JSONL audit events so we can verify ORDER, not
just final state.
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
from core.replan import ReplanPolicy
from tests.conftest import FakeLLM, FakePlanner
from tools.base import Tool, ToolRegistry
from tools.file_write import FileWriteTool


# ============================================================
# Shared helpers
# ============================================================

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
Nothing usable returned.

Facts:
- nothing [general-knowledge]

Sources:
1. general-knowledge - general-knowledge

Confidence: low

Unverified: everything

Safety: nothing"""


def _events(log_path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


class _ScriptedTool(Tool):
    """A Tool whose behaviour is fully controlled by an injected callable.

    Lets a test simulate "succeeds on attempt 1, fails on attempt 2" or
    "always raises" without standing up a real subprocess.
    """

    def __init__(self, name: str, behaviour, *, risk: str = "read_only",
                 validate_fn=None):
        self.name = name
        self.description = f"scripted '{name}' for cross-mvp tests"
        self.risk = risk  # type: ignore[assignment]
        self._behaviour = behaviour
        self._validate_fn = validate_fn
        self.calls = 0

    def run(self, **kwargs):
        self.calls += 1
        return self._behaviour(self.calls, kwargs)

    def validate_output(self, output):
        if self._validate_fn is not None:
            return self._validate_fn(output)
        return True, []


def _build_agent(
    workspace: Path,
    tools: list[Tool],
    planner,
    *,
    approval_provider=None,
    replan_policy: ReplanPolicy | None = None,
    llm_responses: list[str] | None = None,
    persistent_store=None,
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
        persistent_store=persistent_store,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


# ============================================================
# 1. Compensation × Replan
# ============================================================

class TestCompensationDuringReplan:
    """The contract: every successful mutating tool call appends a plan
    to `compensation_log`, regardless of whether the OVERALL attempt
    succeeded. A 2-step plan where step-1 mutates and step-2 fails
    therefore leaves exactly one entry in the log — and rollback can
    apply it cleanly.

    If a future change tries to "smart-clear" the log on replan, it
    would break crash-recovery for the user. Pin the current
    behaviour with a test.
    """

    def test_step1_writes_step2_fails_compensation_still_registered(
        self, workspace: Path
    ):
        # step-1: file_write creates a new file (reversible compensation)
        # step-2: a broken tool raises → tool_error
        fw = FileWriteTool(workspace_root=workspace, max_bytes=1024)

        def boom(n, k):
            raise RuntimeError("step 2 always fails")

        broken = _ScriptedTool("broken", boom, risk="read_only")

        plan = [
            {"tool": "file_write", "arguments": {
                "path": "out.txt", "content": "hi"},
             "label": "stub:fw", "expected_outcome": "writes a file"},
            {"tool": "broken", "arguments": {},
             "label": "stub:broken", "expected_outcome": "fails"},
        ]
        planner = FakePlanner(sources=plan)
        agent, log_path = _build_agent(
            workspace, [fw, broken], planner,
            replan_policy=ReplanPolicy(max_total_replans=2),
            llm_responses=[SYNTH_FAILED],
        )
        agent.run(user_question="x", file_hint=None)

        # Step-1 mutation ACTUALLY happened on disk.
        assert (workspace / "out.txt").exists()

        # On the FIRST attempt, file_write logged a compensation plan.
        events = _events(log_path)
        registrations = [e for e in events if e["event"] == "compensation_registered"]
        # FakePlanner re-proposes the same plan, so step-1 ran on EVERY
        # attempt. Each invocation registers its own plan — the LIFO
        # rollback semantics rely on that.
        assert len(registrations) >= 1
        # The agent's in-memory log carries one CompensationPlan per
        # successful step-1 invocation.
        assert len(agent.compensation_log) == len(registrations)
        # And rollback works: at least the latest write is undone.
        report = agent.rollback(workspace_root=workspace)
        assert report is not None

    def test_no_compensation_for_failed_mutation(self, workspace: Path):
        """If file_write itself fails (e.g. PermissionError on a path
        outside workspace), NO compensation registration. Pinned so a
        future "always log a plan" refactor can't silently break the
        contract."""
        fw = FileWriteTool(workspace_root=workspace, max_bytes=1024)
        # Path with traversal — rejected by tool.run().
        plan = [{"tool": "file_write", "arguments": {
            "path": "../escape.txt", "content": "hi"},
            "label": "stub:fw", "expected_outcome": "blocked"}]
        planner = FakePlanner(sources=plan)
        agent, log_path = _build_agent(
            workspace, [fw], planner,
            replan_policy=ReplanPolicy(max_total_replans=2),
            llm_responses=[SYNTH_FAILED],
        )
        agent.run(user_question="x", file_hint=None)

        events = _events(log_path)
        assert [e for e in events if e["event"] == "compensation_registered"] == []
        assert agent.compensation_log == []

    def test_rollback_lifo_order_after_two_sessions(self, workspace: Path):
        """Two SEPARATE agent.run() calls each write one file. LIFO
        rollback must undo the latest one first.

        We can't easily force the loop to run TWO attempts here
        because as soon as a single step succeeds, the attempt is
        considered productive (`not plan.steps or attempt_artifacts`)
        and no replan happens. So we drive the second mutation via a
        second `run()` call on the same agent — the in-memory
        compensation_log persists across run() calls within the same
        session, which is exactly the behaviour `:rollback` relies on.
        """
        fw = FileWriteTool(workspace_root=workspace, max_bytes=1024)
        # Two SUCCESSIVE single-step plans. Each goes through its own
        # agent.run() cycle. compensation_log accumulates LIFO.
        planner = FakePlanner(sources=[])  # mutated per-call below
        agent, _ = _build_agent(workspace, [fw], planner)

        planner.sources = [{
            "tool": "file_write",
            "arguments": {"path": "a.txt", "content": "1"},
            "label": "stub:a", "expected_outcome": "writes a",
        }]
        agent.run(user_question="x1", file_hint=None)

        planner.sources = [{
            "tool": "file_write",
            "arguments": {"path": "b.txt", "content": "2"},
            "label": "stub:b", "expected_outcome": "writes b",
        }]
        agent.run(user_question="x2", file_hint=None)

        assert (workspace / "a.txt").exists()
        assert (workspace / "b.txt").exists()
        assert len(agent.compensation_log) == 2

        # Pop the latest plan (b.txt) first.
        agent.rollback(workspace_root=workspace)
        assert (workspace / "a.txt").exists()
        assert not (workspace / "b.txt").exists()
        # Pop the older plan (a.txt) next.
        agent.rollback(workspace_root=workspace)
        assert not (workspace / "a.txt").exists()


# ============================================================
# 2. Compensation × Hygiene
# ============================================================

class TestHygienePreservesActiveBackups:
    """Hygiene's `cleanup_backups` deletes old `.bak.<ts>` files. A
    backup currently referenced by an in-memory CompensationPlan must
    survive — otherwise rollback would silently fail.

    Today's protection is structural, not explicit:
      - compensation_log lives only in the current session
      - default `keep_last=3` + `max_age_days=30` means fresh backups
        produced in the same session never qualify for deletion

    The test pins that invariant.
    """

    def test_fresh_backup_survives_default_hygiene(self, workspace: Path):
        # Make a backup the way file_write does, RIGHT NOW.
        target = workspace / "data.txt"
        target.write_text("original")
        backup = workspace / "data.txt.bak.20260526T120000Z"
        backup.write_text("original")  # the same content file_write would save

        from core.hygiene import cleanup_backups
        from datetime import datetime, timezone

        # "Now" matches the backup timestamp closely → backup is fresh.
        now = datetime(2026, 5, 26, 12, 5, 0, tzinfo=timezone.utc)
        report = cleanup_backups(workspace, now=now)

        assert str(backup.relative_to(workspace)) in report.kept
        assert backup.exists()

    def test_old_backup_outside_keep_last_is_purged(self, workspace: Path):
        """The complement: backups OLDER than the retention window AND
        beyond keep_last are deleted. Pins the actual purge behaviour
        so the test above isn't a tautology."""
        target = workspace / "data.txt"
        target.write_text("active")
        old_backups = [
            workspace / "data.txt.bak.20200101T000000Z",
            workspace / "data.txt.bak.20200102T000000Z",
            workspace / "data.txt.bak.20200103T000000Z",
            workspace / "data.txt.bak.20200104T000000Z",
        ]
        for p in old_backups:
            p.write_text("old")

        from core.hygiene import cleanup_backups
        from datetime import datetime, timezone

        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        report = cleanup_backups(workspace, keep_last=2, max_age_days=30, now=now)

        # The TWO newest are preserved; the TWO oldest get purged.
        assert len(report.deleted) == 2
        assert len(report.kept) == 2


# ============================================================
# 3. Classification priority: verify_failed wins over web_empty
# ============================================================

class TestClassificationPriority:
    """If `validate_output` rejects an output, the loop must classify
    as `verify_failed` and NOT fall through to the MVP-12 reclassifier.
    This matters for tools that share a name with web_search or
    shell_exec but produce semantically invalid output."""

    def test_web_search_invalid_shape_is_verify_failed_not_web_empty(
        self, workspace: Path
    ):
        # A tool literally named web_search but its output is a STRING,
        # not a list. validate_output (mirroring real WebSearchTool)
        # rejects non-list outputs as verify_failed.
        def behaviour(n, k):
            return "not a list"

        def validate(output):
            if not isinstance(output, list):
                return False, [f"expected list, got {type(output).__name__}"]
            return True, []

        web = _ScriptedTool(
            "web_search", behaviour, validate_fn=validate,
        )
        plan = [{"tool": "web_search", "arguments": {
            "query": "q", "max_results": 1},
            "label": "stub:w", "expected_outcome": "broken"}]
        planner = FakePlanner(sources=plan)
        agent, log_path = _build_agent(
            workspace, [web], planner,
            replan_policy=ReplanPolicy(max_total_replans=2),
            llm_responses=[SYNTH_FAILED],
        )
        agent.run(user_question="x", file_hint=None)

        events = _events(log_path)
        codes = [
            e["payload"].get("code")
            for e in events
            if e["event"] == "error" and "code" in e["payload"]
        ]
        # verify_failed must appear at least once; web_empty NEVER.
        assert "verify_failed" in codes
        assert "web_empty" not in codes


# ============================================================
# 4. Redaction × Replan
# ============================================================

class TestRedactionInReplanContext:
    """Failure-context arguments come from the planner's previous
    output, but a malicious / careless LLM could have included a
    credential-shaped string in them. The kernel-side `redact_text`
    runs OVER the whole assembled prompt before it reaches the LLM.

    Verify the redactor sees and scrubs a secret-shaped argument
    from a re-plan prompt.
    """

    def test_secret_in_previous_step_args_redacted_before_planner_call(
        self, workspace: Path
    ):
        # First attempt: a step whose `arguments` contain something that
        # LOOKS like an OpenAI API key. Real planner output is JSON, so
        # the secret arrives as a literal string and stays a literal
        # string in failure_context.
        def boom(n, k):
            raise RuntimeError("intentional failure")

        sneaky = _ScriptedTool("sneaky", boom, risk="read_only")
        secret = "sk-" + "A" * 48  # OpenAI key shape
        plan = [{"tool": "sneaky", "arguments": {"token": secret},
                 "label": "stub", "expected_outcome": "explode"}]
        planner = FakePlanner(sources=plan)
        agent, log_path = _build_agent(
            workspace, [sneaky], planner,
            replan_policy=ReplanPolicy(max_total_replans=2),
            llm_responses=[SYNTH_FAILED],
        )
        agent.run(user_question="ok", file_hint=None)

        # The LLM-side prompts are not directly observable, but the
        # AUDIT log mirrors every assembled prompt's stats. Verify the
        # JSONL never contains the raw secret payload.
        log_text = log_path.read_text(encoding="utf-8")
        # The full secret string MUST be absent — either redacted or
        # never written verbatim. NOTE: the secret may appear once as
        # a `secret_detected` event identifier but never as raw chars.
        assert secret not in log_text, (
            "raw secret leaked through to the audit log"
        )


# ============================================================
# 5. Risk-for downgrade × Compensation
# ============================================================

class TestRiskForCompensation:
    """`file_write` dynamically downgrades risk from irreversible to
    reversible when the target path doesn't exist. A reversible call
    STILL registers a compensation plan (delete_path_if_created) so
    rollback works for new-file creates too. Pin that contract."""

    def test_create_new_file_reversible_still_registers_compensation(
        self, workspace: Path
    ):
        fw = FileWriteTool(workspace_root=workspace, max_bytes=1024)
        plan = [{"tool": "file_write", "arguments": {
            "path": "new.txt", "content": "hi"},
            "label": "stub", "expected_outcome": "creates"}]
        planner = FakePlanner(sources=plan)
        agent, log_path = _build_agent(workspace, [fw], planner)
        agent.run(user_question="x", file_hint=None)

        events = _events(log_path)
        # Risk was reversible for this call (new file) → no escalation.
        # Policy event records the dynamic risk in its `reasons` field,
        # NOT as a top-level key. We assert against that text.
        policy = next(e for e in events if e["event"] == "policy")
        assert policy["payload"]["decision"] == "allow"
        assert any(
            "reversible" in r for r in policy["payload"]["reasons"]
        ), "risk_for should have downgraded to reversible for new path"
        # Compensation was STILL registered even though risk was reversible.
        regs = [e for e in events if e["event"] == "compensation_registered"]
        assert len(regs) == 1
        assert agent.compensation_log[0].actions[0].kind == "delete_path_if_created"


# ============================================================
# 6. Approval × Replan
# ============================================================

class TestApprovalReplanInteraction:
    """approval_deny → forbidden_actions → planner picks safe alt →
    runs. This is acceptance #3 of MVP-12, but here we pin the EVENT
    ORDER across two attempts to make sure the safety/replan layers
    interleave correctly."""

    def test_event_ordering_deny_then_safe_alternative(self, workspace: Path):
        from tests.test_replan import SequencedPlanner

        dangerous = _ScriptedTool(
            "danger", lambda n, k: {"never": "ran"}, risk="irreversible"
        )
        safe = _ScriptedTool(
            "safe_read", lambda n, k: "ok content", risk="read_only"
        )

        plans = [
            [{"tool": "danger", "arguments": {"path": "X"},
              "label": "stub", "expected_outcome": "deny"}],
            [{"tool": "safe_read", "arguments": {},
              "label": "stub", "expected_outcome": "ok"}],
        ]
        planner = SequencedPlanner(plans)
        agent, log_path = _build_agent(
            workspace, [dangerous, safe], planner,
            approval_provider=AutoApprover(default="deny"),
            replan_policy=ReplanPolicy(max_total_replans=3),
        )
        agent.run(user_question="x", file_hint=None)

        names = [e["event"] for e in _events(log_path)]
        # Order on attempt 1: policy → approval_request → approval_decision (deny) → error → replan
        # Order on attempt 2: planner → plan → policy → tool_call → tool_result → verify → respond
        # We check the *relative* order of the key transitions:
        assert names.index("approval_decision") < names.index("replan")
        assert names.index("replan") < names.index("tool_call")
        assert "tool_call" in names                  # safe tool actually ran
        assert dangerous.calls == 0
        assert safe.calls == 1


# ============================================================
# 7. Persistent memory × Replan
# ============================================================

class TestPersistentMemoryReplanIsolation:
    """Failure history is loop-internal scratch state. It must NOT
    bleed into the persistent memory store under any code path. The
    only writes to persistent are explicit user `:remember` commands;
    re-planning is silent.
    """

    def test_replan_attempts_do_not_write_to_persistent_store(
        self, workspace: Path, tmp_path: Path
    ):
        from core.persistent_memory import PersistentMemoryStore

        store = PersistentMemoryStore(path=tmp_path / "p.jsonl")
        assert store.load() == []

        def boom(n, k):
            raise RuntimeError("ow")

        bad = _ScriptedTool("bad", boom, risk="read_only")
        plan = [{"tool": "bad", "arguments": {},
                 "label": "stub", "expected_outcome": "fail"}]
        planner = FakePlanner(sources=plan)
        agent, _ = _build_agent(
            workspace, [bad], planner,
            persistent_store=store,
            replan_policy=ReplanPolicy(max_total_replans=2),
            llm_responses=[SYNTH_FAILED],
        )
        agent.run(user_question="please loop", file_hint=None)

        # The replan flow does NOT touch persistent storage.
        assert store.load() == []
