"""Tests for brain/skills/workflow_runner.py — WorkflowRunner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from brain.skills.job import Job, JobStatus
from brain.skills.models import (
    AcceptanceCheck,
    Profession,
    Workflow,
    WorkflowStep,
)
from brain.skills.verifier import Verifier
from brain.skills.workflow_runner import (
    JobOutcome,
    ToolResult,
    ToolRunner,
    WorkflowRunner,
)


# ════════════════════════════════════════════════════════════════════
# Fakes
# ════════════════════════════════════════════════════════════════════

@dataclass
class FakeToolRunner:
    """Programmable tool runner — tests script its responses by tool name."""

    responses: dict[str, ToolResult]
    calls: list[tuple[str, dict]] = None

    def __post_init__(self) -> None:
        self.calls = []

    def run(self, tool_name: str, params: dict) -> ToolResult:
        self.calls.append((tool_name, dict(params)))
        return self.responses.get(
            tool_name,
            ToolResult(success=False, error=f"no fake for {tool_name}"),
        )


class FakeLLM:
    """LLM stub that echoes a fixed rewrite."""

    def __init__(self, rewrite: str = "Improved version of the text.") -> None:
        self.rewrite = rewrite
        self.calls: list[dict] = []

    def call(self, context: dict) -> dict:
        self.calls.append(context)
        return {
            "action":     "respond",
            "content":    self.rewrite,
            "confidence": 0.9,
            "reasoning":  "stub",
        }


# ════════════════════════════════════════════════════════════════════
# Helper — build a text_editor-shaped Profession in code
# ════════════════════════════════════════════════════════════════════

def _make_profession(
    *,
    checks: list[AcceptanceCheck] | None = None,
) -> Profession:
    workflow = Workflow(steps=[
        WorkflowStep(
            id="read",
            description="Read source DOCX",
            action="tool_call",
            tool="docx_reader",
            params={"action": "extract_text"},
        ),
        WorkflowStep(
            id="edit",
            description="Rewrite text",
            action="llm_rewrite",
            depends_on=["read"],
        ),
        WorkflowStep(
            id="write",
            description="Write edited DOCX",
            action="tool_call",
            tool="docx_writer",
            depends_on=["edit"],
        ),
        WorkflowStep(
            id="verify",
            description="Check acceptance",
            action="verify",
            depends_on=["write"],
        ),
        WorkflowStep(
            id="deliver",
            description="Deliver",
            action="deliver",
            depends_on=["verify"],
        ),
    ])
    return Profession(
        id="text_editor",
        name="Text Editor (English)",
        cluster="text",
        language="en",
        required_capabilities=["read_docx", "llm_text_rewrite", "write_docx"],
        system_prompt="You are an editor.",
        workflow=workflow,
        acceptance_criteria=checks or [],
        price_range_usd=(3.0, 25.0),
    )


def _make_job(attachment: str = "/tmp/in.docx") -> Job:
    return Job(
        brief="Please edit the attached document.",
        source="email",
        client_id="client@example.com",
        attachments=[attachment],
    )


# ════════════════════════════════════════════════════════════════════
# Plan instantiation
# ════════════════════════════════════════════════════════════════════

class TestBuildPlan:

    def test_workflow_steps_become_plan_steps(self):
        prof = _make_profession()
        job = _make_job()
        runner = WorkflowRunner(llm=None, tools=FakeToolRunner({}))
        plan = runner.build_plan(prof, job)
        assert len(plan.steps) == 5
        # String IDs → integer IDs, depends_on preserved
        assert plan.steps[0].depends_on == []
        assert plan.steps[1].depends_on == [0]
        assert plan.steps[4].depends_on == [3]

    def test_workflow_id_carried_in_params(self):
        prof = _make_profession()
        runner = WorkflowRunner(llm=None, tools=FakeToolRunner({}))
        plan = runner.build_plan(prof, _make_job())
        wf_ids = [s.params.get("workflow_id") for s in plan.steps]
        assert wf_ids == ["read", "edit", "write", "verify", "deliver"]


# ════════════════════════════════════════════════════════════════════
# End-to-end happy path
# ════════════════════════════════════════════════════════════════════

class TestHappyPath:

    def test_runs_full_workflow_with_fakes(self, tmp_path):
        prof = _make_profession(checks=[
            AcceptanceCheck(
                kind="word_count_delta",
                params={"range_pct": [-0.50, 0.50]},
            ),
        ])
        attachment = str(tmp_path / "src.docx")
        job = _make_job(attachment=attachment)

        tools = FakeToolRunner({
            "docx_reader": ToolResult(
                success=True,
                output="Original sentence one. Original sentence two.",
            ),
            "docx_writer": ToolResult(
                success=True,
                output={"path": str(tmp_path / "src.edited.docx"), "paragraphs": 1},
            ),
        })
        llm = FakeLLM(rewrite="Edited sentence one. Edited sentence two.")
        runner = WorkflowRunner(llm=llm, tools=tools, verifier=Verifier())

        outcome = runner.execute(prof, job)

        assert outcome.final_status is JobStatus.DELIVERED
        assert outcome.verifier_report is not None
        assert outcome.verifier_report.passed
        # The tool runner saw both reader and writer
        names = [c[0] for c in tools.calls]
        assert names[:2] == ["docx_reader", "docx_writer"]
        # The deliverable path was generated correctly
        assert str(tmp_path / "src.edited.docx") in (outcome.deliverables or [])
        # LLM was called once
        assert len(llm.calls) == 1

    def test_writer_gets_paragraphs_and_overwrite_defaults(self, tmp_path):
        prof = _make_profession()
        job = _make_job(attachment=str(tmp_path / "src.docx"))
        tools = FakeToolRunner({
            "docx_reader": ToolResult(
                success=True, output="Hello world.",
            ),
            "docx_writer": ToolResult(
                success=True, output={"path": str(tmp_path / "out.docx")},
            ),
        })
        runner = WorkflowRunner(
            llm=FakeLLM(rewrite="Hello there.\n\nGoodbye."),
            tools=tools,
        )
        runner.execute(prof, job)
        writer_call = next(c for c in tools.calls if c[0] == "docx_writer")
        _, params = writer_call
        # default-injected by the runner
        assert params["action"] == "create"
        assert params["overwrite"] is True
        # paragraphs split on blank lines
        assert params["paragraphs"] == ["Hello there.", "Goodbye."]


# ════════════════════════════════════════════════════════════════════
# Failure handling
# ════════════════════════════════════════════════════════════════════

class TestFailures:

    def test_reader_failure_aborts_workflow(self):
        prof = _make_profession()
        job = _make_job()
        tools = FakeToolRunner({
            "docx_reader": ToolResult(
                success=False, error="file not found",
            ),
        })
        runner = WorkflowRunner(llm=FakeLLM(), tools=tools)
        outcome = runner.execute(prof, job)
        assert outcome.final_status is JobStatus.FAILED
        # Subsequent steps were skipped, never executed
        assert all(
            c[0] != "docx_writer" for c in tools.calls
        ), f"writer should NOT have run, got calls={tools.calls}"

    def test_empty_llm_response_fails_edit(self):
        prof = _make_profession()
        job = _make_job()
        tools = FakeToolRunner({
            "docx_reader": ToolResult(success=True, output="Some text."),
        })

        class SilentLLM:
            def call(self, ctx):
                return {"action": "respond", "content": "", "confidence": 1.0, "reasoning": ""}

        runner = WorkflowRunner(llm=SilentLLM(), tools=tools)
        outcome = runner.execute(prof, job)
        assert outcome.final_status is JobStatus.FAILED

    def test_verifier_failure_marks_job_failed(self, tmp_path):
        prof = _make_profession(checks=[
            AcceptanceCheck(
                kind="contains_no",
                params={"forbidden_substrings": ["Edited"]},
            ),
        ])
        job = _make_job(attachment=str(tmp_path / "src.docx"))
        tools = FakeToolRunner({
            "docx_reader": ToolResult(success=True, output="Original."),
            "docx_writer": ToolResult(
                success=True, output={"path": str(tmp_path / "out.docx")},
            ),
        })
        runner = WorkflowRunner(llm=FakeLLM(rewrite="Edited sentence."), tools=tools)
        outcome = runner.execute(prof, job)
        assert outcome.final_status is JobStatus.FAILED
        assert outcome.verifier_report is not None
        assert not outcome.verifier_report.passed

    def test_no_llm_when_workflow_needs_one(self):
        prof = _make_profession()
        job = _make_job()
        tools = FakeToolRunner({
            "docx_reader": ToolResult(success=True, output="Some text."),
        })
        runner = WorkflowRunner(llm=None, tools=tools)
        outcome = runner.execute(prof, job)
        assert outcome.final_status is JobStatus.FAILED

    def test_unknown_action_fails_softly(self):
        wf = Workflow(steps=[
            WorkflowStep(id="a", description="?", action="frobnicate"),
        ])
        prof = Profession(
            id="x", name="X", cluster="text", language="en",
            required_capabilities=[], system_prompt="?",
            workflow=wf, acceptance_criteria=[], price_range_usd=(1.0, 2.0),
        )
        runner = WorkflowRunner(llm=None, tools=FakeToolRunner({}))
        outcome = runner.execute(prof, _make_job())
        assert outcome.final_status is JobStatus.FAILED
        assert any("unknown action" in (r.error or "") for r in outcome.step_records)


# ════════════════════════════════════════════════════════════════════
# Outcome shape
# ════════════════════════════════════════════════════════════════════

class TestOutcome:

    def test_outcome_records_each_step(self, tmp_path):
        prof = _make_profession()
        job = _make_job(attachment=str(tmp_path / "src.docx"))
        tools = FakeToolRunner({
            "docx_reader": ToolResult(success=True, output="Hello world."),
            "docx_writer": ToolResult(
                success=True, output={"path": str(tmp_path / "out.docx")},
            ),
        })
        runner = WorkflowRunner(llm=FakeLLM(rewrite="Hi world."), tools=tools)
        outcome = runner.execute(prof, job)
        # 4 records (no acceptance_criteria → verify still runs and passes
        # vacuously; deliver still runs).
        workflow_ids = [r.workflow_id for r in outcome.step_records]
        assert workflow_ids == ["read", "edit", "write", "verify", "deliver"]
