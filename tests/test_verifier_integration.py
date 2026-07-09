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

import pytest

from core.approval import AutoApprover
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.policy import PolicyGate
from core.verifier import DISCLAIMER_FULLY_UNVERIFIED, DISCLAIMER_NO_CHAIN
from tools.base import ToolRegistry
from tools.file_read import FileReadTool
from tools.web_search import WebSearchTool
from tests.conftest import FakeLLM, FakePlanner


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
# Verified answer
# ============================================================

class TestVerifiedAnswer:
    def test_cited_answer_gets_verified_marker(self, workspace: Path):
        (workspace / "doc.txt").write_text("hello world", encoding="utf-8")
        llm_answer = (
            "Conclusion: file says hello [file:doc.txt].\n"
            "Facts: hello is in doc.txt [file:doc.txt].\n"
            "Sources: doc.txt"
        )
        agent, log_path = _agent(
            workspace,
            llm_response=llm_answer,
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "doc.txt"},
                "label": "file:doc.txt",
            }],
        )
        answer = agent.run("what does doc.txt say", file_hint="doc.txt")
        annotated = agent.last_verification.annotated_answer
        # All `[file:doc.txt]` were rewritten to `[verified:file:doc.txt]`.
        assert "[verified:file:doc.txt]" in annotated
        assert "[file:doc.txt]" not in annotated or annotated.count("[file:doc.txt]") == 0
        # No disclaimer when there's at least one verified claim.
        assert DISCLAIMER_FULLY_UNVERIFIED not in answer
        assert DISCLAIMER_NO_CHAIN not in answer

    def test_last_verification_populated(self, workspace: Path):
        (workspace / "doc.txt").write_text("x", encoding="utf-8")
        llm_answer = "fact [file:doc.txt]."
        agent, _ = _agent(
            workspace, llm_response=llm_answer,
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "doc.txt"},
                "label": "file:doc.txt",
            }],
        )
        agent.run("read it", file_hint="doc.txt")
        report = agent.last_verification
        assert report is not None
        assert report.verified_chunks >= 1
        assert report.fully_unverified is False


# ============================================================
# Uncited answer → fully unverified + disclaimer
# ============================================================

class TestUncitedAnswer:
    def test_no_citations_gets_disclaimer(self, workspace: Path):
        (workspace / "doc.txt").write_text("hello", encoding="utf-8")
        llm_answer = (
            "Conclusion: file mentions hello.\n"
            "Facts: there is content. The file exists.\n"
            "Sources: doc.txt"
        )
        agent, _ = _agent(
            workspace, llm_response=llm_answer,
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "doc.txt"},
                "label": "file:doc.txt",
            }],
        )
        answer = agent.run("read", file_hint="doc.txt")
        annotated = agent.last_verification.annotated_answer
        # Chain non-empty but no citations -> "fully_unverified" disclaimer.
        assert "[unverified]" in annotated
        assert DISCLAIMER_FULLY_UNVERIFIED in answer

    def test_no_chain_uses_no_chain_disclaimer(self, workspace: Path):
        # Zero planner steps -> chain stays empty -> different disclaimer.
        agent, _ = _agent(
            workspace,
            llm_response="A general-knowledge answer with no citations.",
            canned_sources=[],
        )
        answer = agent.run("a general question")
        assert DISCLAIMER_NO_CHAIN in answer
        assert DISCLAIMER_FULLY_UNVERIFIED not in answer


# ============================================================
# verification JSONL event
# ============================================================

class TestVerificationEvent:
    def test_event_emitted_once(self, workspace: Path):
        (workspace / "doc.txt").write_text("x", encoding="utf-8")
        agent, log_path = _agent(
            workspace,
            llm_response="cited [file:doc.txt].",
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "doc.txt"},
                "label": "file:doc.txt",
            }],
        )
        agent.run("q", file_hint="doc.txt")
        events = _events(log_path)
        verifications = [e for e in events if e["event"] == "verification"]
        assert len(verifications) == 1
        p = verifications[0]["payload"]
        assert p["verified_chunks"] >= 1
        assert p["fully_unverified"] is False
        assert "verdicts" in p

    def test_disclaimer_set_flag_in_log(self, workspace: Path):
        (workspace / "doc.txt").write_text("x", encoding="utf-8")
        agent, log_path = _agent(
            workspace,
            llm_response="no citations here.",
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "doc.txt"},
                "label": "file:doc.txt",
            }],
        )
        agent.run("q", file_hint="doc.txt")
        events = _events(log_path)
        ver = [e for e in events if e["event"] == "verification"][0]
        assert ver["payload"]["disclaimer_set"] is True


# ============================================================
# verifier_enabled=False
# ============================================================

class TestVerifierDisabled:
    def test_disabled_returns_draft_unchanged(self, workspace: Path):
        (workspace / "doc.txt").write_text("x", encoding="utf-8")
        original = "raw answer with citation [file:doc.txt] still here."
        agent, log_path = _agent(
            workspace, llm_response=original,
            canned_sources=[{
                "tool": "file_read",
                "arguments": {"path": "doc.txt"},
                "label": "file:doc.txt",
            }],
            verifier_enabled=False,
        )
        answer = agent.run("q", file_hint="doc.txt")
        # No annotation when verifier is off.
        assert "[verified:" not in answer
        assert "[unverified]" not in answer
        # Original citation untouched.
        assert "[file:doc.txt]" in answer
        # No verification event.
        events = _events(log_path)
        assert not [e for e in events if e["event"] == "verification"]
        # last_verification is None.
        assert agent.last_verification is None


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
