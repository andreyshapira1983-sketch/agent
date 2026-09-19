"""Core data models for the agent (§12.1 of the architecture).

Every object that crosses a domain boundary must be one of these models.
This keeps the loop, tools, and policy layer talking the same language.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from .ids import new_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- intent / planning ----------

class Goal(BaseModel):
    id: str = Field(default_factory=lambda: new_id("goal"))
    description: str
    success_criteria: str
    parent_goal_id: str | None = None
    status: Literal["pending", "in_progress", "done", "failed"] = "pending"
    priority: int = 5
    deadline: datetime | None = None


class Task(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    parent_goal_id: str | None = None
    description: str
    status: Literal["pending", "in_progress", "done", "failed"] = "pending"
    priority: int = 5
    deadline: datetime | None = None
    dependencies: list[str] = Field(default_factory=list)
    owner_agent: str = "main"
    created_at: datetime = Field(default_factory=_now)


class PlanStep(BaseModel):
    id: str = Field(default_factory=lambda: new_id("step"))
    plan_id: str
    order: int
    action_spec: dict[str, Any]
    preconditions: list[str] = Field(default_factory=list)
    expected_outcome: str
    status: Literal["pending", "in_progress", "done", "failed"] = "pending"
    retry_count: int = 0


class Plan(BaseModel):
    id: str = Field(default_factory=lambda: new_id("plan"))
    goal_id: str
    steps: list[PlanStep] = Field(default_factory=list)
    version: int = 1
    status: Literal["pending", "in_progress", "done", "failed"] = "pending"
    strategy: str = "linear"
    created_at: datetime = Field(default_factory=_now)


# ---------- perception / action / tools ----------

class Observation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("obs"))
    source: str
    modality: Literal["text", "file", "image", "audio"] = "text"
    content: Any
    confidence: float = 1.0
    timestamp: datetime = Field(default_factory=_now)
    provenance: str = "user"


class Action(BaseModel):
    id: str = Field(default_factory=lambda: new_id("act"))
    step_id: str
    type: Literal["tool_call", "llm_synthesize", "output"]
    tool_name: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    side_effects: Literal["none", "read", "write", "external"] = "none"
    timestamp: datetime = Field(default_factory=_now)


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tc"))
    action_id: str
    tool_name: str
    arguments: dict[str, Any]
    started_at: datetime = Field(default_factory=_now)
    timeout: float = 30.0
    idempotency_key: str = Field(default_factory=lambda: new_id("idem"))


class ToolResult(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tr"))
    tool_call_id: str
    status: Literal["success", "error", "timeout"]
    output: Any | None = None
    error: str | None = None
    latency_ms: int = 0
    cost: float = 0.0
    completed_at: datetime = Field(default_factory=_now)


# ---------- governance ----------

class PolicyDecision(BaseModel):
    id: str = Field(default_factory=lambda: new_id("pol"))
    policy_id: str
    subject: str
    action: str
    decision: Literal["allow", "deny", "escalate"]
    reasons: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=_now)


class ApprovalRequest(BaseModel):
    """§12.1 + §7 Human Approval. Emitted by the loop whenever the
    PolicyGate escalates an Action; the bound `ApprovalProvider` answers
    with an `ApprovalDecision`.
    """

    id: str = Field(default_factory=lambda: new_id("appr"))
    action_id: str
    step_id: str
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk: Literal["read_only", "reversible", "irreversible", "external"]
    reasons: list[str] = Field(default_factory=list)
    summary: str = ""
    policy_decision_id: str | None = None
    requested_at: datetime = Field(default_factory=_now)


class ApprovalDecision(BaseModel):
    id: str = Field(default_factory=lambda: new_id("appd"))
    request_id: str
    decision: Literal["approve", "deny", "abort"]
    responder: str = "user"  # "user" | "auto" | "timeout"
    reasons: list[str] = Field(default_factory=list)
    raw_input: str | None = None
    decided_at: datetime = Field(default_factory=_now)


# ---------- memory ----------

class MemoryRecord(BaseModel):
    """§12.1 Memory Record. MVP-4 uses only the `working` type."""

    id: str = Field(default_factory=lambda: new_id("mem"))
    type: Literal["working", "episodic", "semantic", "procedural"] = "working"
    content: Any
    tags: list[str] = Field(default_factory=list)
    owner: str = "session"
    ttl_seconds: int | None = None
    created_at: datetime = Field(default_factory=_now)
    # MIR-074 root fix: WHO asserted this. It always existed as a write-policy
    # input but was never persisted, so every stored record lost its origin —
    # and the MIR-046 independence rule then demoted EVERY memory citation to
    # topic-only forever (the measured all-history zero of verified memory
    # citations). `user-explicit` records are human assertions and may verify;
    # agent-auto ones stay non-independent by doctrine.
    source: str | None = None

    # --- importance tracking (for archive scoring) ---
    # How many times this record was retrieved and injected into a prompt.
    access_count: int = 0
    # MIR-074 (operator ruling 2026-08-03): CAUSAL credits — the record was
    # cited as [memory:<id>] in an answer chunk the verifier marked
    # `verified`, i.e. retrieved → changed the answer → independently
    # checked. Injection alone (access_count) is a near-zero signal; THIS
    # is the strong one, and it is what keeps a record active.
    causal_use: int = 0
    # Last time this record was retrieved (None = never used after creation).
    last_accessed_at: datetime | None = None
    # Computed importance score 0.0-1.0. Updated by archive_low_value_memory().
    importance: float = 0.5
    # True = record has been moved to the archive store; active store never
    # contains archived records.
    archived: bool = False


# ---------- failure ----------

class ErrorObject(BaseModel):
    id: str = Field(default_factory=lambda: new_id("err"))
    source: str
    code: str
    message: str
    severity: Literal["info", "warning", "error", "fatal"] = "error"
    recoverable: bool = False
    context: dict[str, Any] = Field(default_factory=dict)
    stack_trace: str | None = None
    timestamp: datetime = Field(default_factory=_now)
