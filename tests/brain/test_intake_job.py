"""End-to-end tests for Brain.intake_job — routing a Job through a Profession."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from brain.core import Brain
from brain.policy import PolicyDecision, PolicyEngine, PolicyRule, PolicyVerdict
from brain.skills.job import Job, JobStatus, JobStore
from brain.skills.models import (
    AcceptanceCheck,
    Capability,
    Profession,
    Workflow,
    WorkflowStep,
)
from brain.skills.registry import SkillRegistry
from brain.skills.workflow_runner import ToolResult, ToolRunner


# ════════════════════════════════════════════════════════════════════
# Fakes
# ════════════════════════════════════════════════════════════════════

class FakeMemory:
    """Tiny MemoryInterface stub — Brain doesn't touch memory for intake_job."""
    def store(self, *a, **kw): pass
    def retrieve(self, *a, **kw): return []
    def get_history(self, *a, **kw): return []
    def get_facts(self, *a, **kw): return []
    def status(self): return {"history": 0, "facts": 0}


class FakeLLM:
    def __init__(self, rewrite: str = "Polished version of the text.") -> None:
        self.rewrite = rewrite
        self.calls = 0
    def call(self, _ctx):
        self.calls += 1
        return {
            "action": "respond",
            "content": self.rewrite,
            "confidence": 0.9,
            "reasoning": "fake",
        }


@dataclass
class FakeToolRunner:
    responses: dict
    calls: list = None
    def __post_init__(self): self.calls = []
    def run(self, tool_name: str, params: dict) -> ToolResult:
        self.calls.append((tool_name, dict(params)))
        return self.responses.get(
            tool_name, ToolResult(success=False, error=f"no fake for {tool_name}"),
        )


# ════════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def registry() -> SkillRegistry:
    reg = SkillRegistry()
    for cap_id in ("read_docx", "llm_text_rewrite", "write_docx"):
        reg.register_capability(Capability(id=cap_id, description="x"))
    wf = Workflow(steps=[
        WorkflowStep(id="read",   description="r", action="tool_call",   tool="docx_reader"),
        WorkflowStep(id="edit",   description="e", action="llm_rewrite", depends_on=["read"]),
        WorkflowStep(id="write",  description="w", action="tool_call",   tool="docx_writer",
                     depends_on=["edit"]),
        WorkflowStep(id="verify", description="v", action="verify",      depends_on=["write"]),
    ])
    prof = Profession(
        id="text_editor",
        name="Text Editor",
        cluster="text",
        language="en",
        required_capabilities=["read_docx", "llm_text_rewrite", "write_docx"],
        system_prompt="Edit gently.",
        workflow=wf,
        acceptance_criteria=[
            AcceptanceCheck(
                kind="word_count_delta",
                params={"range_pct": [-0.50, 0.50]},
            ),
        ],
        price_range_usd=(3.0, 25.0),
    )
    reg.register_profession(prof)
    return reg


@pytest.fixture
def tools(tmp_path):
    return FakeToolRunner(responses={
        "docx_reader": ToolResult(success=True, output="Hello world from source."),
        "docx_writer": ToolResult(
            success=True,
            output={"path": str(tmp_path / "out.edited.docx")},
        ),
    })


@pytest.fixture
def brain(registry, tools):
    return Brain(
        llm=FakeLLM(),
        memory=FakeMemory(),
        skill_registry=registry,
        tool_runner=tools,
    )


@pytest.fixture
def job_store(tmp_path) -> JobStore:
    store = JobStore(tmp_path / "jobs.db")
    yield store
    store.close()


# ════════════════════════════════════════════════════════════════════
# Tests
# ════════════════════════════════════════════════════════════════════

class TestIntakeJob:

    def test_happy_path_delivers(self, brain, tmp_path):
        attachment = tmp_path / "src.docx"
        attachment.write_text("placeholder", encoding="utf-8")
        job = Job(
            brief="Please edit this English document.",
            source="email",
            client_id="me@example.com",
            attachments=[str(attachment)],
        )
        outcome = brain.intake_job(job)
        assert outcome.final_status is JobStatus.DELIVERED
        assert outcome.profession_id == "text_editor"
        assert outcome.verifier_report is not None
        assert outcome.verifier_report.passed

    def test_no_match_declines(self, brain):
        # Strip the registry's only profession to force a non-match.
        brain._skill_registry._professions.clear()  # noqa: SLF001 — test access
        job = Job(brief="anything", source="email", client_id="x")
        outcome = brain.intake_job(job)
        assert outcome.final_status is JobStatus.DECLINED
        assert outcome.profession_id == ""

    def test_forced_profession_id_bypasses_match(self, brain, tmp_path):
        attachment = tmp_path / "src.docx"
        attachment.write_text("x", encoding="utf-8")
        job = Job(brief="trash brief", source="email", client_id="x",
                  attachments=[str(attachment)])
        outcome = brain.intake_job(job, profession_id="text_editor")
        assert outcome.final_status is JobStatus.DELIVERED

    def test_unknown_forced_profession_declines(self, brain):
        job = Job(brief="x", source="email", client_id="x")
        outcome = brain.intake_job(job, profession_id="nonexistent")
        assert outcome.final_status is JobStatus.DECLINED

    def test_requires_runner_and_registry(self):
        b = Brain(llm=FakeLLM(), memory=FakeMemory())
        with pytest.raises(RuntimeError, match="skill_registry"):
            b.intake_job(Job(brief="x", source="email", client_id="x"))


# ────────────────────────────────────────────────────────────────────
# JobStore integration
# ────────────────────────────────────────────────────────────────────

class TestIntakeJobWithStore:

    def test_status_transitions_persisted(
        self, registry, tools, job_store, tmp_path
    ):
        brain = Brain(
            llm=FakeLLM(),
            memory=FakeMemory(),
            skill_registry=registry,
            tool_runner=tools,
            job_store=job_store,
        )
        attachment = tmp_path / "src.docx"
        attachment.write_text("x", encoding="utf-8")
        job = Job(brief="please edit", source="email", client_id="me@e.com",
                  attachments=[str(attachment)])
        job_store.create(job)

        outcome = brain.intake_job(job)
        assert outcome.final_status is JobStatus.DELIVERED

        reloaded = job_store.get(job.id)
        assert reloaded.status is JobStatus.DELIVERED
        assert reloaded.profession_id == "text_editor"
        # Note trail contains both lifecycle transitions
        joined_notes = " | ".join(reloaded.notes)
        assert "matched" in joined_notes
        assert "in_progress" in joined_notes
        assert "delivered" in joined_notes

    def test_no_match_persists_declined(
        self, registry, tools, job_store
    ):
        registry._professions.clear()  # noqa: SLF001
        brain = Brain(
            llm=FakeLLM(),
            memory=FakeMemory(),
            skill_registry=registry,
            tool_runner=tools,
            job_store=job_store,
        )
        job = Job(brief="x", source="email", client_id="x")
        job_store.create(job)
        outcome = brain.intake_job(job)
        assert outcome.final_status is JobStatus.DECLINED
        assert job_store.get(job.id).status is JobStatus.DECLINED


# ────────────────────────────────────────────────────────────────────
# Policy interaction
# ────────────────────────────────────────────────────────────────────

class TestIntakeJobPolicy:

    def test_policy_can_deny_intake(self, registry, tools, tmp_path):
        # Custom rule: deny any job_intake targeting "text_editor"
        deny_rule = PolicyRule(
            id="deny_text_editor_intake",
            description="block text_editor",
            predicate=lambda req: req.kind == "job_intake" and req.target == "text_editor",
            verdict=PolicyDecision.DENY,
            reason="text_editor is forbidden for the test",
        )
        engine = PolicyEngine([deny_rule])
        brain = Brain(
            llm=FakeLLM(),
            memory=FakeMemory(),
            skill_registry=registry,
            tool_runner=tools,
            policy=engine,
        )
        attachment = tmp_path / "src.docx"
        attachment.write_text("x", encoding="utf-8")
        job = Job(brief="please edit", source="email", client_id="x",
                  attachments=[str(attachment)])
        outcome = brain.intake_job(job)
        assert outcome.final_status is JobStatus.DECLINED
        assert any("policy DENY" in n for n in outcome.notes)
