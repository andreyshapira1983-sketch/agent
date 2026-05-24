"""
brain/skills/workflow_runner.py — Workflow → Plan instantiator + executor.

Turns a Profession's static Workflow template into a concrete plan and walks
it step-by-step. This is the orchestration layer the Brain dispatches to
when a Job is matched to a Profession.

Responsibilities
────────────────
1. Convert `Workflow.steps` (string-id graph) into a `brain.planner.Plan`
   (integer-id graph) preserving dependencies.
2. Resolve placeholder params (e.g. inject `Job.attachments[0]` into the
   first tool_call's `file_path`).
3. Drive execution: feed each step into the right runner (LLM, tool,
   verifier, deliver), record results, advance the Plan.
4. On completion, return a `JobOutcome` summarising what happened so
   `Brain.intake_job` can update `JobStore`.

Non-responsibilities
────────────────────
- Does NOT call OpenAI directly. Uses an injected `LLMInterface`.
- Does NOT touch the file system except through tools.
- Does NOT decide policy. Policy verdicts come from `PolicyEngine` via
  the tool layer; runner only respects the outcome.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..interfaces.llm_interface import LLMInterface
from ..planner import Plan, Planner, StepStatus
from .job import Job, JobStatus
from .models import Profession, WorkflowStep
from .verifier import LLMJudge, Verifier, VerifierReport

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
# Outcome record
# ════════════════════════════════════════════════════════════════════

@dataclass
class StepRecord:
    step_id: int
    workflow_id: str
    action: str
    tool: str | None
    success: bool
    output: Any = None
    error: str | None = None


@dataclass
class JobOutcome:
    """What the WorkflowRunner produced for one Job."""

    job_id:             str
    profession_id:      str
    final_status:       JobStatus
    deliverables:       list[str] = field(default_factory=list)
    step_records:       list[StepRecord] = field(default_factory=list)
    verifier_report:    VerifierReport | None = None
    notes:              list[str] = field(default_factory=list)
    deliverable_text:   str = ""
    # Set to True when intake_job stops at the policy gate because a rule
    # requested human approval. The job is parked in MATCHED state; the
    # operator can resume it once the approval is granted.
    requires_approval:  bool = False

    def add_note(self, note: str) -> None:
        self.notes.append(note)
        logger.info("[WorkflowRunner] %s", note)


# ════════════════════════════════════════════════════════════════════
# Tool runner protocol — minimal injection surface
# ════════════════════════════════════════════════════════════════════

class ToolRunner:
    """The runner uses this thin protocol — Brain wires it to ToolHandler
    or a synchronous ToolExecutor wrapper in tests."""

    def run(self, tool_name: str, params: dict) -> "ToolResult":
        raise NotImplementedError


@dataclass
class ToolResult:
    success: bool
    output: Any = None
    error: str | None = None


# ════════════════════════════════════════════════════════════════════
# Runner
# ════════════════════════════════════════════════════════════════════

class WorkflowRunner:
    """Executes a Profession's Workflow against a Job.

    The runner is the smallest possible orchestrator: it does not retry,
    does not branch, does not pause for approval. Those concerns belong to
    upstream layers (BrainLoop, PolicyEngine, AuditLog).

    Usage:
        runner = WorkflowRunner(
            llm=llm_adapter,
            tools=tool_runner,
            verifier=Verifier(llm_judge=llm_judge),
        )
        outcome = runner.execute(profession, job)
        # outcome.final_status is one of JobStatus.{DELIVERED, FAILED}
    """

    def __init__(
        self,
        llm: LLMInterface | None,
        tools: ToolRunner,
        verifier: Verifier | None = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._verifier = verifier or Verifier()

    # ────────────────────────────────────────────────────────────────

    def build_plan(self, profession: Profession, job: Job) -> Plan:
        """Convert Profession.workflow into a brain.planner.Plan.

        We do NOT mutate the Profession's Workflow — Plan is a fresh
        per-Job object. String IDs become integer IDs; `depends_on` is
        rewritten to use the new integers.
        """
        wf = profession.workflow
        id_map: dict[str, int] = {s.id: idx for idx, s in enumerate(wf.steps)}

        steps_for_planner: list[dict[str, Any]] = []
        for wf_step in wf.steps:
            params = dict(wf_step.params)
            # Stamp the step with its original workflow_id so the runner
            # can recognise it when iterating.
            steps_for_planner.append({
                "description": wf_step.description,
                "action":      wf_step.action,
                "params":      {
                    **params,
                    "tool":        wf_step.tool,
                    "workflow_id": wf_step.id,
                    "job_id":      job.id,
                },
                "depends_on":  [id_map[d] for d in wf_step.depends_on if d in id_map],
            })

        planner = Planner()
        plan = planner.create_plan(goal=profession.name, steps=steps_for_planner)
        return plan

    # ────────────────────────────────────────────────────────────────

    def execute(self, profession: Profession, job: Job) -> JobOutcome:
        """Run the workflow end-to-end, returning a JobOutcome."""
        outcome = JobOutcome(
            job_id=job.id,
            profession_id=profession.id,
            final_status=JobStatus.IN_PROGRESS,
        )
        outcome.add_note(f"start profession={profession.id} job={job.id}")

        plan = self.build_plan(profession, job)
        planner = Planner()
        # We built a plan above with its own internal planner; rebuild here
        # to manage it directly through next_step / mark_done.
        planner._active_plan = plan  # noqa: SLF001 — internal pairing

        # State carried between steps (read result of `read` step feeds the
        # `edit` step, edit result feeds the `write` step, etc.).
        state: dict[str, Any] = {
            "job":              job,
            "profession":       profession,
            "source_text":      "",
            "edited_text":      "",
            "deliverable_path": "",
        }
        # Seed source_text from first attachment if present
        if job.attachments:
            state["attachment_path"] = job.attachments[0]

        guard = 0
        while planner.has_active_plan():
            guard += 1
            if guard > 100:
                outcome.add_note("guard tripped — too many iterations")
                outcome.final_status = JobStatus.FAILED
                break

            step = planner.next_step()
            if step is None:
                break

            workflow_id = str(step.params.get("workflow_id") or step.id)
            success, output, error = self._dispatch_step(
                action=step.action,
                params=step.params,
                state=state,
                profession=profession,
                job=job,
                outcome=outcome,
            )
            outcome.step_records.append(StepRecord(
                step_id=step.id,
                workflow_id=workflow_id,
                action=step.action,
                tool=step.params.get("tool"),
                success=success,
                output=output if success else None,
                error=error,
            ))
            if success:
                planner.mark_done(step.id, result=output)
            else:
                outcome.add_note(f"step '{workflow_id}' failed: {error}")
                planner.mark_failed(step.id, error=error or "unknown error",
                                    skip_dependents=True)

        # Plan finished — translate to JobStatus.
        if any(r.action == "verify" and r.success is False for r in outcome.step_records):
            outcome.final_status = JobStatus.FAILED
        elif any(not r.success for r in outcome.step_records):
            outcome.final_status = JobStatus.FAILED
        else:
            outcome.final_status = JobStatus.DELIVERED

        outcome.deliverables = list(state.get("deliverables", []))
        outcome.deliverable_text = state.get("edited_text", "")
        outcome.add_note(f"final={outcome.final_status.value}")
        return outcome

    # ────────────────────────────────────────────────────────────────
    # Dispatch
    # ────────────────────────────────────────────────────────────────

    def _dispatch_step(
        self,
        action: str,
        params: dict,
        state: dict,
        profession: Profession,
        job: Job,
        outcome: JobOutcome,
    ) -> tuple[bool, Any, str | None]:
        """Run one step. Returns (success, output, error)."""
        if action == "tool_call":
            return self._run_tool_call(params, state, profession)
        if action == "llm_rewrite":
            return self._run_llm_rewrite(state, profession, job)
        if action == "verify":
            return self._run_verify(state, profession, outcome)
        if action == "deliver":
            return self._run_deliver(state)
        # Unknown action — fail soft so future YAML stays compatible
        return False, None, f"unknown action: {action}"

    # ── tool_call ────────────────────────────────────────────────

    def _run_tool_call(
        self,
        params: dict,
        state: dict,
        profession: Profession,
    ) -> tuple[bool, Any, str | None]:
        tool_name = str(params.get("tool") or "")
        if not tool_name:
            return False, None, "workflow tool_call has no `tool`"
        wf_id = str(params.get("workflow_id") or "")

        # Compose the actual tool params: drop our internal keys + inject state.
        forwarded = {
            k: v for k, v in params.items()
            if k not in {"tool", "workflow_id", "job_id"}
        }

        # Substitute well-known placeholders depending on the workflow step.
        # For the text_editor profession this maps:
        #   step "read"  → docx_reader  with file_path = attachment_path
        #   step "write" → docx_writer  with file_path = deliverable_path,
        #                                paragraphs = edited paragraphs
        if tool_name == "docx_reader":
            forwarded.setdefault("file_path", state.get("attachment_path", ""))
        elif tool_name == "docx_writer":
            # Decide where to write the deliverable.
            if not state.get("deliverable_path"):
                src = state.get("attachment_path", "")
                if src:
                    p = Path(src)
                    state["deliverable_path"] = str(p.with_name(f"{p.stem}.edited{p.suffix}"))
                else:
                    state["deliverable_path"] = "deliverable.docx"
            forwarded.setdefault("file_path", state["deliverable_path"])
            # Convert edited_text into paragraph list if no explicit list given
            if "paragraphs" not in forwarded:
                edited = state.get("edited_text", "") or ""
                forwarded["paragraphs"] = [
                    p.strip()
                    for p in edited.split("\n\n")
                    if p.strip()
                ]
            forwarded.setdefault("action", "create")
            forwarded.setdefault("overwrite", True)
        elif tool_name == "email":
            # Deliver step uses the email tool; fill in to= / subject /
            # body / attachments from job + state.
            forwarded.setdefault("to", state["job"].client_id if "job" in state else "")
            forwarded.setdefault("subject", "Edited: " + (state["job"].brief[:80] if "job" in state else ""))
            forwarded.setdefault(
                "body",
                "Edited document attached. Summary of changes available on request.",
            )
            if state.get("deliverable_path"):
                forwarded.setdefault("attachments", [state["deliverable_path"]])

        result = self._tools.run(tool_name, forwarded)

        # Side effect: capture source_text from docx_reader output
        if result.success and tool_name == "docx_reader":
            text = result.output if isinstance(result.output, str) else (
                result.output.get("text") if isinstance(result.output, dict) else str(result.output)
            )
            state["source_text"] = text or ""

        # Side effect: record deliverable path
        if result.success and tool_name == "docx_writer":
            path = (result.output or {}).get("path") if isinstance(result.output, dict) else None
            if path:
                state.setdefault("deliverables", []).append(path)

        return result.success, result.output, result.error

    # ── llm_rewrite ──────────────────────────────────────────────

    def _run_llm_rewrite(
        self,
        state: dict,
        profession: Profession,
        job: Job,
    ) -> tuple[bool, Any, str | None]:
        if self._llm is None:
            return False, None, "no LLM available for llm_rewrite step"
        source = state.get("source_text") or ""
        if not source.strip():
            return False, None, "no source text to rewrite"
        # Build a one-shot context — we don't reuse Brain.think() here
        # because the workflow runner runs OUTSIDE the conversational
        # Brain cycle for now (post-Phase-1 we may merge them).
        context = {
            "input": f"Rewrite the following text per your instructions:\n\n{source}",
            "goals": [
                {"goal": profession.system_prompt, "priority": 1},
            ],
            "history": [],
            "facts":   [],
            "session_id": f"workflow:{job.id}",
            "profession_id": profession.id,
        }
        try:
            llm_output = self._llm.call(context)
        except Exception as exc:  # noqa: BLE001
            return False, None, f"LLM error: {type(exc).__name__}: {exc}"
        rewritten = ""
        if isinstance(llm_output, dict):
            rewritten = str(llm_output.get("content") or llm_output.get("text") or "")
        if not rewritten.strip():
            return False, None, "LLM returned empty rewrite"
        state["edited_text"] = rewritten
        return True, rewritten, None

    # ── verify ───────────────────────────────────────────────────

    def _run_verify(
        self,
        state: dict,
        profession: Profession,
        outcome: JobOutcome,
    ) -> tuple[bool, Any, str | None]:
        report = self._verifier.verify(
            checks=profession.acceptance_criteria,
            original=state.get("source_text", ""),
            deliverable=state.get("edited_text", ""),
        )
        outcome.verifier_report = report
        if not report.passed:
            return False, report.to_dict(), report.summary
        return True, report.to_dict(), None

    # ── deliver ──────────────────────────────────────────────────

    def _run_deliver(self, state: dict) -> tuple[bool, Any, str | None]:
        # Deliver currently piggybacks on a tool_call (email). When the
        # YAML uses action="deliver" directly, just acknowledge the
        # deliverable already exists on disk.
        deliverable = state.get("deliverable_path") or ""
        if deliverable:
            return True, {"delivered": deliverable}, None
        return False, None, "no deliverable to send"
