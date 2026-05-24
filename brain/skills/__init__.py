"""
brain/skills — Skill / Capability Library.

The agent's professional identity. A single Brain can play many roles —
text editor, translator, Python scripter — by loading the right Profession
from this package.

Layered model:
    Capability     atomic ability the agent has  (usually maps 1:1 to a tool)
    Profession     bundle of capabilities + workflow + acceptance criteria
    Workflow       template Plan a Profession uses to execute a Job
    AcceptanceCheck verifier rule: did the deliverable meet the criterion?
    Job            single client request (brief + attachments + deliverables)

Adding a new freelance profession = adding one YAML file to the
professions/ directory; no Python changes required.
"""

from .models import (
    AcceptanceCheck,
    AcceptanceResult,
    Capability,
    Profession,
    Workflow,
    WorkflowStep,
)
from .registry import SkillRegistry
from .job import Job, JobStatus, JobStore
from .knowledge import KnowledgeBase, KnowledgeFact, KnowledgeIndex
from .portfolio import Portfolio, PortfolioEntry, ProfessionStats
from .verifier import LLMJudge, Verifier, VerifierReport
from .workflow_runner import (
    JobOutcome,
    StepRecord,
    ToolResult,
    ToolRunner,
    WorkflowRunner,
)

__all__ = [
    "AcceptanceCheck",
    "AcceptanceResult",
    "Capability",
    "Job",
    "JobOutcome",
    "JobStatus",
    "JobStore",
    "KnowledgeBase",
    "KnowledgeFact",
    "KnowledgeIndex",
    "LLMJudge",
    "Portfolio",
    "PortfolioEntry",
    "Profession",
    "ProfessionStats",
    "SkillRegistry",
    "StepRecord",
    "ToolResult",
    "ToolRunner",
    "Verifier",
    "VerifierReport",
    "Workflow",
    "WorkflowRunner",
    "WorkflowStep",
]
