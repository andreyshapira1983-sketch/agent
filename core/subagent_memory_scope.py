"""MVP-18.1  Autonomous Subagent Proposal Contract.

The agent autonomously determines when a subagent is needed and produces a
``SubagentProposal`` that explicitly describes *what* the subagent may read,
write, use, and spend — before any execution takes place.

This file satisfies the architecture_audit ``subagent_memory_scope`` check:
  evidence : core/subagent_memory_scope.py  (this file)
  tests    : tests/test_subagent_memory_scope.py

Design principles
-----------------
1. NO real subagent execution here.  This is the proposal layer only.
2. Every proposal has ``approval_required=True`` by default.
3. The agent, not the human, creates the proposal from a goal/event.
4. Memory, tool, and budget scopes are fully explicit — nothing is implicit.
5. ``needs_delegation()`` is a fast rule-based pre-check (no LLM).
6. ``propose_subagent()`` calls the LLM for structured JSON and falls back
   gracefully if the LLM returns invalid output.

Typical flow
------------
goal / event
  → needs_delegation(goal)              # fast keyword heuristic
  → propose_subagent(goal, llm=...)     # LLM → SubagentProposal
  → approval_inbox.add(...)             # human reviews
  → after approval: dry-run / real run  # future layer

Command surface (main.py)
-------------------------
  :subagent-proposal <goal> [--submit]
      --submit  also adds the proposal to the approval inbox
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from core.ids import new_id


# ── internal helpers ───────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """Extract first JSON object from *text*. Returns None on failure."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
        if not isinstance(data, dict):
            return None
        return data
    except json.JSONDecodeError:
        return None


# ── scope contracts ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MemoryScope:
    """What a subagent may read from and write to persistent memory."""

    read_tags: tuple[str, ...]
    write_tags: tuple[str, ...]
    write_requires_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "read_tags": list(self.read_tags),
            "write_tags": list(self.write_tags),
            "write_requires_review": self.write_requires_review,
        }


@dataclass(frozen=True)
class ToolScope:
    """Which tools a subagent may invoke."""

    allowed_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    read_only: bool = True

    def __post_init__(self) -> None:
        overlap = set(self.allowed_tools) & set(self.forbidden_tools)
        if overlap:
            raise ValueError(
                f"tools cannot be both allowed and forbidden: {sorted(overlap)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_tools": list(self.allowed_tools),
            "forbidden_tools": list(self.forbidden_tools),
            "read_only": self.read_only,
        }


@dataclass(frozen=True)
class BudgetScope:
    """Numeric hard limits for a subagent execution."""

    max_model_calls: int = 3
    max_web_fetches: int = 5
    max_file_writes: int = 0
    max_cycles: int = 1

    def __post_init__(self) -> None:
        for name in ("max_model_calls", "max_web_fetches", "max_file_writes", "max_cycles"):
            val = getattr(self, name)
            if val < 0:
                raise ValueError(f"{name} must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_web_fetches": self.max_web_fetches,
            "max_file_writes": self.max_file_writes,
            "max_cycles": self.max_cycles,
        }


# ── proposal ───────────────────────────────────────────────────────────────────

RiskLevel = Literal["low", "medium", "high"]
ProposalStatus = Literal["proposed", "not_needed", "llm_error"]


@dataclass(frozen=True)
class SubagentProposal:
    """Fully explicit description of a proposed subagent delegation."""

    task_goal: str
    why_needed: str
    proposed_role: str
    memory_scope: MemoryScope
    tool_scope: ToolScope
    budget_scope: BudgetScope
    risk_level: RiskLevel
    expected_output: str
    narrative: str = ""          # human-readable description (from LLM, in goal's language)
    approval_required: bool = True
    proposal_id: str = field(default_factory=lambda: new_id("sap"))
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "task_goal": self.task_goal,
            "why_needed": self.why_needed,
            "proposed_role": self.proposed_role,
            "narrative": self.narrative,
            "memory_scope": self.memory_scope.to_dict(),
            "tool_scope": self.tool_scope.to_dict(),
            "budget_scope": self.budget_scope.to_dict(),
            "risk_level": self.risk_level,
            "expected_output": self.expected_output,
            "approval_required": self.approval_required,
            "created_at": self.created_at,
        }

    def user_summary(self) -> str:
        sep = "─" * 52
        read_scope = ", ".join(self.memory_scope.read_tags) or "—"
        write_scope = (
            ", ".join(self.memory_scope.write_tags)
            if self.memory_scope.write_tags
            else "—  (нет записи)"
        )
        allowed = ", ".join(self.tool_scope.allowed_tools) or "—"
        forbidden = ", ".join(self.tool_scope.forbidden_tools) or "—"
        budget = (
            f"{self.budget_scope.max_model_calls} model calls / "
            f"{self.budget_scope.max_web_fetches} web fetches / "
            f"{self.budget_scope.max_cycles} cycle(s)"
        )
        approval_str = (
            "YES — ожидает подтверждения" if self.approval_required else "не требуется (auto)"
        )
        lines = [
            sep,
            f"Предлагаемый подагент : {self.proposed_role}",
            f"Зачем нужен           : {self.why_needed}",
        ]
        if self.narrative:
            lines.append(f"Описание              : {self.narrative}")
        lines += [
            f"Read scope            : {read_scope}",
            f"Write scope           : {write_scope}",
            f"Write requires review : {self.memory_scope.write_requires_review}",
            f"Allowed tools         : {allowed}",
            f"Forbidden tools       : {forbidden}",
            f"Read-only             : {self.tool_scope.read_only}",
            f"Budget                : {budget}",
            f"Risk                  : {self.risk_level}",
            f"Expected output       : {self.expected_output}",
            f"Approval required     : {approval_str}",
            f"ID                    : {self.proposal_id}",
            sep,
        ]
        return "\n".join(lines)


@dataclass
class SubagentProposalResult:
    """Output of propose_subagent()."""

    status: ProposalStatus
    proposal: SubagentProposal | None = None
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    raw_response: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "proposed" and self.proposal is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "proposal": self.proposal.to_dict() if self.proposal else None,
            "reason": self.reason,
            "warnings": list(self.warnings),
        }

    def user_summary(self) -> str:
        if self.status == "not_needed":
            return f"Делегация не нужна: {self.reason}"
        if self.status == "llm_error":
            lines = [f"Не удалось сформировать proposal: {self.reason}"]
            for w in self.warnings:
                lines.append(f"  ⚠  {w}")
            return "\n".join(lines)
        # status == "proposed"
        lines: list[str] = []
        for w in self.warnings:
            lines.append(f"⚠  {w}")
        if self.proposal:
            lines.append(self.proposal.user_summary())
        return "\n".join(lines)


# ── public API ─────────────────────────────────────────────────────────────────

_DELEGATION_KEYWORDS: tuple[str, ...] = (
    "monitor", "watch", "continuously", "repeatedly", "scheduled",
    "batch", "scan", "crawl", "poll", "track all", "analyse all",
    "analyze all", "process all", "multiple sources", "ongoing",
    "background", "periodic", "автоматически", "следи", "мониторь",
    "отслеживай", "периодически", "в фоне",
)


def needs_delegation(goal: str) -> bool:
    """Lightweight rule-based pre-check.  No LLM call.

    Returns True when the goal *likely* needs a dedicated subagent.
    The LLM in propose_subagent() makes the authoritative decision.
    """
    if not isinstance(goal, str):
        return False
    g = goal.casefold()
    return any(kw in g for kw in _DELEGATION_KEYWORDS)


def make_default_proposal(goal: str) -> SubagentProposal:
    """Build a conservative default proposal without any LLM call.

    Used as a test fixture and as the fallback when the LLM fails.
    All scopes are read-only / minimal by default.
    """
    return SubagentProposal(
        task_goal=goal,
        why_needed="Task requires dedicated execution scope separate from the main agent.",
        proposed_role="GeneralSubagent",
        memory_scope=MemoryScope(
            read_tags=("project", "fact"),
            write_tags=(),
            write_requires_review=True,
        ),
        tool_scope=ToolScope(
            allowed_tools=("file_read", "web_search"),
            forbidden_tools=("shell_exec", "file_write"),
            read_only=True,
        ),
        budget_scope=BudgetScope(
            max_model_calls=2,
            max_web_fetches=3,
            max_file_writes=0,
            max_cycles=1,
        ),
        risk_level="low",
        expected_output="A summary report of findings, no side-effects.",
        approval_required=True,
    )


def propose_subagent(
    goal: str,
    *,
    llm: Any,
    logger: Any = None,
) -> SubagentProposalResult:
    """Generate a SubagentProposal from a natural-language goal.

    Calls the LLM for a structured JSON proposal.  Falls back to
    ``status="llm_error"`` (with a warning) if the LLM response is not
    valid JSON.  Returns ``status="not_needed"`` if the LLM concludes that
    the goal can be handled directly without delegation.
    """
    _emit(logger, "subagent_proposal_start", {"goal": goal})

    raw = llm.complete(
        system=_SYSTEM_PROMPT,
        user=f"Goal: {goal}",
        max_tokens=1024,
        temperature=0.0,
    )

    data = _parse_json_object(raw)
    if data is None:
        _emit(logger, "subagent_proposal_llm_error", {
            "goal": goal,
            "raw_preview": raw[:200],
        })
        return SubagentProposalResult(
            status="llm_error",
            reason="LLM did not return valid JSON",
            warnings=[f"raw preview: {raw[:120]}"],
            raw_response=raw,
        )

    # LLM may say delegation is not needed
    if not data.get("needed", True):
        _emit(logger, "subagent_proposal_not_needed", {
            "goal": goal,
            "reason": data.get("reason", ""),
        })
        return SubagentProposalResult(
            status="not_needed",
            reason=data.get("reason") or "LLM determined no subagent is needed",
            raw_response=raw,
        )

    # Parse scopes from LLM response with safe defaults
    try:
        proposal = _parse_proposal(goal, data)
    except Exception as exc:
        _emit(logger, "subagent_proposal_parse_error", {
            "goal": goal,
            "error": str(exc),
        })
        return SubagentProposalResult(
            status="llm_error",
            reason=f"proposal parse error: {exc}",
            warnings=[str(exc)],
            raw_response=raw,
        )

    _emit(logger, "subagent_proposal_ready", proposal.to_dict())
    return SubagentProposalResult(
        status="proposed",
        proposal=proposal,
        raw_response=raw,
    )


# ── internals ─────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an autonomous agent initiative evaluator.

Task: given a goal, decide whether it requires a dedicated subagent and
produce a precise, safety-conscious proposal.

Rules:
- Respond ONLY with a single JSON object. No prose, no markdown code fences.
- Detect the language of the goal. Use that language for: narrative, why_needed,
  expected_output. Use English or CamelCase for proposed_role.
- Be conservative: default read_only=true, max_file_writes=0, approval_required=true.
- risk_level must be one of: low, medium, high.
- allowed_tools and forbidden_tools must NOT overlap.
- Always forbid: shell_exec, send_message, send_proposal, reply_to_client, contact_client
  unless the goal explicitly and safely requires them.
- If the goal says "без разрешения" / "without permission" / "without approval",
  set approval_required=true and add the dangerous action to forbidden_tools.

If the goal CAN be handled directly (one-shot lookup, single analysis, single-step task):
  {"needed": false, "reason": "<why no subagent is needed, in goal's language>"}

If a dedicated subagent IS needed:
{
  "needed": true,
  "narrative": "<2-3 sentences in the goal's language: what this subagent does, why it is needed, what it must NOT do>",
  "why_needed": "<concise reason in goal's language>",
  "proposed_role": "<DescriptiveCamelCaseRoleName>",
  "memory_read_tags": ["<tag>"],
  "memory_write_tags": [],
  "write_requires_review": true,
  "allowed_tools": ["web_search", "file_read"],
  "forbidden_tools": ["shell_exec", "send_message", "file_write"],
  "read_only": true,
  "max_model_calls": 5,
  "max_web_fetches": 10,
  "max_file_writes": 0,
  "max_cycles": 1,
  "risk_level": "medium",
  "expected_output": "<what the subagent must return, in goal's language>",
  "approval_required": true
}
"""


def _parse_proposal(goal: str, data: dict[str, Any]) -> SubagentProposal:
    risk = str(data.get("risk_level") or "low")
    if risk not in ("low", "medium", "high"):
        risk = "low"

    read_tags = tuple(str(t) for t in (data.get("memory_read_tags") or []))
    write_tags = tuple(str(t) for t in (data.get("memory_write_tags") or []))
    allowed = tuple(str(t) for t in (data.get("allowed_tools") or ["file_read"]))
    forbidden = tuple(str(t) for t in (data.get("forbidden_tools") or []))
    # Resolve any overlap conservatively: overlap → stays forbidden
    allowed = tuple(t for t in allowed if t not in set(forbidden))

    return SubagentProposal(
        task_goal=goal,
        why_needed=str(data.get("why_needed") or "delegation required"),
        proposed_role=str(data.get("proposed_role") or "GeneralSubagent"),
        narrative=str(data.get("narrative") or ""),
        memory_scope=MemoryScope(
            read_tags=read_tags,
            write_tags=write_tags,
            write_requires_review=bool(data.get("write_requires_review", True)),
        ),
        tool_scope=ToolScope(
            allowed_tools=allowed,
            forbidden_tools=forbidden,
            read_only=bool(data.get("read_only", True)),
        ),
        budget_scope=BudgetScope(
            max_model_calls=max(0, int(data.get("max_model_calls") or 3)),
            max_web_fetches=max(0, int(data.get("max_web_fetches") or 5)),
            max_file_writes=max(0, int(data.get("max_file_writes") or 0)),
            max_cycles=max(1, int(data.get("max_cycles") or 1)),
        ),
        risk_level=risk,  # type: ignore[arg-type]
        expected_output=str(data.get("expected_output") or "subagent output"),
        approval_required=bool(data.get("approval_required", True)),
    )


def _emit(logger: Any, event: str, payload: Any) -> None:
    if logger is not None:
        logger.log(event, payload)
