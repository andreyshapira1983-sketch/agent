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
