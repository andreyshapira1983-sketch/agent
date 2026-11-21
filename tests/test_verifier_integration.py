"""MVP-14.4 — Verifier integrated through AgentLoop.

These tests exercise the END-to-END contract:

  * a draft LLM answer with valid citations comes out [verified:...];
  * an LLM answer with NO citations comes out fully [unverified] +
    disclaimer;
  * the `verification` JSONL event captures the diagnosis;
  * `verifier_enabled=False` returns the draft answer unchanged;
  * `agent.last_verification` is populated.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from core.verifier import DISCLAIMER_NO_CHAIN
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.web_search import WebSearchTool


def _events(p: Path) -> list[dict]:
    out: list[dict] = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _agent(
    workspace: Path,
    *,
    llm_response: str,
    canned_sources: list[dict],
    verifier_enabled: bool = True,
) -> tuple[AgentLoop, Path]:
    reg = ToolRegistry()
    reg.register(FileReadTool(workspace_root=workspace))
    reg.register(WebSearchTool())
    for src in canned_sources:
        src.setdefault("expected_outcome", "executes the planned step")
    llm = FakeLLM(responses=[llm_response])
    planner = FakePlanner(canned_sources)
    trace_id = new_trace_id()
    logger = TraceLogger(
        trace_id=trace_id, log_dir=workspace / "logs", verbose=False
    )
    agent = AgentLoop(
        registry=reg,
        policy=PolicyGate(reg),
        llm=llm,
        logger=logger,
        planner=planner,
        approval_provider=AutoApprover(default="approve"),
        max_replan_attempts=1,
        verifier_enabled=verifier_enabled,
    )
    return agent, workspace / "logs" / f"{trace_id}.jsonl"


# ============================================================
# Sandbox: redaction order
# ============================================================

class TestRedactionOrderingPostVerifier:
    """The defence-in-depth redact_text pass runs AFTER the Verifier.
    Verify a secret slipped into the LLM draft is redacted even when
    Verifier processed it as an [unverified] chunk."""

    def test_secret_in_draft_redacted_after_verify(self, workspace: Path):
        secret = "sk-" + "C" * 48
        llm_answer = f"Conclusion: leaked {secret}.\nFacts: x."
        agent, log_path = _agent(
            workspace, llm_response=llm_answer,
            canned_sources=[],   # empty chain -> [unverified] tags
        )
        answer = agent.run("question")
        # Verifier ran (we see disclaimer for no_chain) AND the secret
        # was redacted on the way out.
        assert DISCLAIMER_NO_CHAIN in answer
        assert secret not in answer
        # Audit trail: a `secret_detected` event for surface=user_output.
        events = _events(log_path)
        secret_events = [
            e for e in events
            if e["event"] == "secret_detected"
            and e["payload"].get("surface") == "user_output"
        ]
        assert secret_events
