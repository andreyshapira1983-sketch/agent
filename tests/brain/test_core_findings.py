"""Regression tests for the seven `brain/core.py` findings.

Each test pins one specific contract identified in the manual audit so the
fix can never silently regress. Naming convention:

    test_finding_N_<short_description>

so a failure points straight at the audit item.
"""

from __future__ import annotations

import math
import pytest

from brain.core import Brain, ThinkResult, _LLMSimilarityAdapter
from brain.interpreter import Interpreter
from brain.interfaces.llm_interface import LLMInterface
from brain.interfaces.memory_interface import MemoryInterface
from brain.policy import (
    ActionRequest,
    PolicyDecision,
    PolicyEngine,
    PolicyRule,
    PolicyVerdict,
)
from brain.privacy import PIIRedactor
from brain.skills.job import Job, JobStatus, JobStore
from brain.skills.models import (
    AcceptanceCheck,
    Capability,
    Profession,
    Workflow,
    WorkflowStep,
)
from brain.skills.registry import SkillRegistry
from brain.skills.verifier import Verifier
from brain.skills.workflow_runner import ToolResult, ToolRunner


# ══════════════════════════════════════════════════════════════════════
# Shared fakes
# ══════════════════════════════════════════════════════════════════════

class _RecordingMemory(MemoryInterface):
    """Captures every store() call so tests can assert order and shape."""
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []  # (session, role, content)
    def store(self, session_id, role, content):
        self.events.append((session_id, role, str(content)))
    def recall_history(self, session_id, limit=10):
        return [
            {"role": role, "content": content}
            for (sid, role, content) in self.events
            if sid == session_id
        ][-limit:]
    def recall_facts(self, query, top_k=5): return []
    def forget(self, session_id): self.events = [e for e in self.events if e[0] != session_id]


class _StubLLM(LLMInterface):
    """LLM that always returns a predetermined dict."""
    def __init__(self, reply: dict) -> None:
        self.reply = reply
        self.last_context: dict | None = None
    def call(self, context):
        self.last_context = context
        return self.reply
    def is_available(self): return True
    @property
    def model_name(self): return "stub"


# ══════════════════════════════════════════════════════════════════════
# Finding 1 — Policy bypass via the LLM's documented `tool_name` key.
# ══════════════════════════════════════════════════════════════════════

def test_finding_1_policy_sees_canonical_tool_name():
    deny = PolicyRule(
        id="deny_docx_writer",
        description="block docx_writer",
        predicate=lambda req: req.kind == "tool_call" and req.target == "docx_writer",
        verdict=PolicyDecision.DENY,
        reason="forbidden in test",
    )
    engine = PolicyEngine([deny])

    # LLM emits the documented `tool_name` key — must still be denied.
    llm = _StubLLM({
        "action": "tool_call",
        "content": {"tool_name": "docx_writer", "params": {"path": "a.docx"}},
        "confidence": 0.9,
        "reasoning": "write",
    })

    brain = Brain(llm=llm, memory=_RecordingMemory(), policy=engine)
    result = brain.think("write a doc", session_id="s")
    assert result.policy_verdict is not None
    assert result.policy_verdict.decision is PolicyDecision.DENY
    assert result.action == "wait"


# ══════════════════════════════════════════════════════════════════════
# Finding 2 — PII must be restored inside tool_call params, not only in
# `respond`/`clarify` strings.
# ══════════════════════════════════════════════════════════════════════

def test_finding_2_pii_restored_in_tool_call_params():
    redactor = PIIRedactor()
    # Pre-seed a session-scoped mapping by redacting an inbound message.
    redacted = redactor.redact("send to me@example.com", session_id="s")
    # Sanity: the placeholder is what the LLM would see.
    assert "[EMAIL_1]" in redacted

    # LLM echoes the placeholder back inside a tool_call payload.
    llm = _StubLLM({
        "action": "tool_call",
        "content": {
            "tool_name": "email",
            "params": {"to": "[EMAIL_1]", "body": "hi [EMAIL_1]"},
        },
        "confidence": 0.9,
        "reasoning": "send",
    })

    brain = Brain(llm=llm, memory=_RecordingMemory(), redactor=redactor)
    result = brain.think("send to me@example.com", session_id="s")

    assert result.action == "tool_call"
    assert result.content["params"]["to"] == "me@example.com"
    assert result.content["params"]["body"] == "hi me@example.com"


# ══════════════════════════════════════════════════════════════════════
# Finding 5 — Verifier must fail-closed when the LLM judge is inconclusive.
# ══════════════════════════════════════════════════════════════════════

class _BorkedLLM(LLMInterface):
    def call(self, _ctx): raise RuntimeError("LLM is on fire")
    def is_available(self): return False
    @property
    def model_name(self): return "borked"


class _GibberishLLM(LLMInterface):
    def call(self, _ctx): return {"content": "the moon is cheese"}
    def is_available(self): return True
    @property
    def model_name(self): return "gibber"


def test_finding_5_adapter_returns_nan_on_exception():
    adapter = _LLMSimilarityAdapter(_BorkedLLM())
    score = adapter.similarity("a", "b")
    assert math.isnan(score)


def test_finding_5_adapter_returns_nan_on_unparseable_reply():
    adapter = _LLMSimilarityAdapter(_GibberishLLM())
    score = adapter.similarity("a", "b")
    assert math.isnan(score)


def test_finding_5_verifier_fails_closed_when_judge_inconclusive():
    class _NanJudge:
        def similarity(self, *_a): return float("nan")

    verifier = Verifier(llm_judge=_NanJudge())
    check = AcceptanceCheck(kind="no_new_facts", params={"min_score": 0.85}, blocking=True)
    report = verifier.verify([check], original="orig text", deliverable="other text")
    assert not report.passed
    assert not report.results[0].passed
    assert "inconclusive" in report.results[0].message


def test_finding_5_verifier_passes_with_note_when_nonblocking():
    class _NanJudge:
        def similarity(self, *_a): return float("nan")

    verifier = Verifier(llm_judge=_NanJudge())
    check = AcceptanceCheck(kind="no_new_facts", params={"min_score": 0.85}, blocking=False)
    report = verifier.verify([check], original="orig", deliverable="other")
    assert report.passed
    assert report.results[0].passed
    assert "inconclusive" in report.results[0].message


# ══════════════════════════════════════════════════════════════════════
# Finding 6 — Explanation must reflect the post-policy verdict.
# ══════════════════════════════════════════════════════════════════════

def test_finding_6_explanation_built_after_policy_gate():
    deny = PolicyRule(
        id="deny_tool",
        description="block the tool",
        predicate=lambda req: req.kind == "tool_call" and req.target == "rm_rf",
        verdict=PolicyDecision.DENY,
        reason="dangerous",
    )
    engine = PolicyEngine([deny])
    llm = _StubLLM({
        "action": "tool_call",
        "content": {"tool_name": "rm_rf", "params": {}},
        "confidence": 0.9,
        "reasoning": "destroy",
    })
    brain = Brain(llm=llm, memory=_RecordingMemory(), policy=engine)
    result = brain.think("clean up everything", session_id="s")

    # Policy turned the action into "wait" — the explanation MUST agree.
    assert result.action == "wait"
    assert result.explanation is not None
    assert result.explanation.action == "wait"


# ══════════════════════════════════════════════════════════════════════
# Finding 7 — User input must be stored AFTER context building, exactly once.
# ══════════════════════════════════════════════════════════════════════

def test_finding_7_user_input_not_in_history_during_same_cycle():
    memory = _RecordingMemory()
    llm = _StubLLM({
        "action": "respond",
        "content": "ok",
        "confidence": 0.9,
        "reasoning": "ack",
    })
    brain = Brain(llm=llm, memory=memory)

    brain.think("hello there", session_id="s")

    # The LLM context for THIS cycle must not have included our input
    # in history (else it would appear twice — once in input, once in history).
    history = llm.last_context.get("history", [])
    assert not any(
        ev.get("role") == "user" and "hello there" in str(ev.get("content", ""))
        for ev in history
    )
    # But memory MUST contain user+assistant after the cycle ends.
    roles_recorded = [role for (_s, role, _c) in memory.events]
    assert "user" in roles_recorded
    assert "assistant" in roles_recorded


# ══════════════════════════════════════════════════════════════════════
# Findings 3 & 4 — REQUIRE_APPROVAL parks job, runner crashes fail safely.
# ══════════════════════════════════════════════════════════════════════

class _FakeLLM(LLMInterface):
    def call(self, _ctx):
        return {"action": "respond", "content": "polished", "confidence": 0.9, "reasoning": "x"}
    def is_available(self): return True
    @property
    def model_name(self): return "fake"


class _CrashingRunner(ToolRunner):
    def run(self, _tool_name, _params):
        raise RuntimeError("simulated tool crash")


def _build_text_editor_registry() -> SkillRegistry:
    reg = SkillRegistry()
    for cap_id in ("read_docx", "llm_text_rewrite", "write_docx"):
        reg.register_capability(Capability(id=cap_id, description="x"))
    wf = Workflow(steps=[
        WorkflowStep(id="read",  description="r", action="tool_call",   tool="docx_reader"),
        WorkflowStep(id="edit",  description="e", action="llm_rewrite", depends_on=["read"]),
        WorkflowStep(id="write", description="w", action="tool_call",   tool="docx_writer",
                     depends_on=["edit"]),
    ])
    reg.register_profession(Profession(
        id="text_editor",
        name="Text Editor",
        cluster="text",
        language="en",
        required_capabilities=["read_docx", "llm_text_rewrite", "write_docx"],
        system_prompt="Edit gently.",
        workflow=wf,
        acceptance_criteria=[],
        price_range_usd=(3.0, 25.0),
    ))
    return reg


def test_finding_3_intake_job_parks_when_policy_requires_approval(tmp_path):
    approval_rule = PolicyRule(
        id="needs_approval",
        description="text_editor needs approval",
        predicate=lambda req: req.kind == "job_intake" and req.target == "text_editor",
        verdict=PolicyDecision.REQUIRE_APPROVAL,
        reason="paid work",
    )
    engine = PolicyEngine([approval_rule])

    class _UnusedRunner(ToolRunner):
        def __init__(self): self.called = False
        def run(self, *_a, **_kw):
            self.called = True
            return ToolResult(success=True, output="")

    runner = _UnusedRunner()
    store = JobStore(tmp_path / "jobs.db")
    try:
        brain = Brain(
            llm=_FakeLLM(),
            memory=_RecordingMemory(),
            skill_registry=_build_text_editor_registry(),
            tool_runner=runner,
            job_store=store,
            policy=engine,
        )
        attachment = tmp_path / "src.docx"
        attachment.write_text("x", encoding="utf-8")
        job = Job(brief="please edit", source="email", client_id="x",
                  attachments=[str(attachment)])
        store.create(job)

        outcome = brain.intake_job(job)
        assert outcome.requires_approval is True
        assert outcome.final_status is JobStatus.MATCHED
        # Runner must NOT have fired — job is parked.
        assert runner.called is False
        # Persisted state mirrors the in-flight outcome.
        reloaded = store.get(job.id)
        assert reloaded.status is JobStatus.MATCHED
    finally:
        store.close()


def test_finding_4_runner_crash_lands_in_failed(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    try:
        brain = Brain(
            llm=_FakeLLM(),
            memory=_RecordingMemory(),
            skill_registry=_build_text_editor_registry(),
            tool_runner=_CrashingRunner(),
            job_store=store,
        )
        attachment = tmp_path / "src.docx"
        attachment.write_text("x", encoding="utf-8")
        job = Job(brief="please edit", source="email", client_id="x",
                  attachments=[str(attachment)])
        store.create(job)

        outcome = brain.intake_job(job)
        # Either the runner caught the crash itself (FAILED), or we caught
        # the propagation. Either way: no stuck IN_PROGRESS.
        assert outcome.final_status is JobStatus.FAILED
        reloaded = store.get(job.id)
        assert reloaded.status is JobStatus.FAILED
    finally:
        store.close()
