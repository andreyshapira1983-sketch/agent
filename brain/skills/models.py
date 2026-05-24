"""
brain/skills/models.py — Data models for the Skill Library.

Everything here is pure data:
    - no I/O
    - no LLM calls
    - no side effects

Loading from YAML / matching / verifying are separate modules
(`registry.py`, `verifier.py`). This file is the schema.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# Capability
# ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Capability:
    """
    Atomic ability the agent has. Most capabilities are backed by exactly
    one tool, but some are backed by the LLM itself (e.g. `llm_text_rewrite`).
    """

    id: str
    description: str
    tool_name: str | None = None  # registered tool, if any backs this capability

    def is_native(self) -> bool:
        """True when the capability is backed by an LLM call, not a tool."""
        return self.tool_name is None


# ────────────────────────────────────────────────────────────────────
# Workflow
# ────────────────────────────────────────────────────────────────────

@dataclass
class WorkflowStep:
    """
    One step of a Profession's workflow template.

    Mirrors brain.planner.Step but lives at the template level — concrete
    Plan steps are instantiated from these when a Job starts.
    """

    id: str
    description: str
    action: str                                # "tool_call" | "llm_rewrite" | "verify" | "deliver"
    tool: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)

    def to_planner_dict(self) -> dict[str, Any]:
        """Shape that brain.planner.Planner.create_plan() expects."""
        return {
            "description": self.description,
            "action":      self.action,
            "params":      {**self.params, "tool": self.tool} if self.tool else dict(self.params),
            "depends_on":  [],   # depends_on uses string IDs; Planner uses int IDs — resolved at plan-build time
            "step_ref":    self.id,
        }


@dataclass
class Workflow:
    """Ordered sequence of WorkflowSteps that a Profession executes."""

    steps: list[WorkflowStep] = field(default_factory=list)

    def step_ids(self) -> list[str]:
        return [s.id for s in self.steps]

    def get(self, step_id: str) -> WorkflowStep | None:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def validate(self) -> list[str]:
        """
        Return a list of validation errors (empty list = OK).

        Checks:
            - step IDs are unique
            - depends_on references exist
            - no self-dependencies
            - no obvious cycles
        """
        errors: list[str] = []

        ids = [s.id for s in self.steps]
        seen: set[str] = set()
        for sid in ids:
            if sid in seen:
                errors.append(f"duplicate step id: '{sid}'")
            seen.add(sid)

        id_set = set(ids)
        for s in self.steps:
            if s.id in s.depends_on:
                errors.append(f"step '{s.id}' depends on itself")
            for dep in s.depends_on:
                if dep not in id_set:
                    errors.append(f"step '{s.id}' depends on unknown step '{dep}'")

        # Lightweight cycle check via DFS
        if self._has_cycle():
            errors.append("workflow contains a cycle")

        return errors

    def _has_cycle(self) -> bool:
        graph = {s.id: list(s.depends_on) for s in self.steps}
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {sid: WHITE for sid in graph}

        def dfs(node: str) -> bool:
            color[node] = GRAY
            for nxt in graph.get(node, []):
                if color.get(nxt, BLACK) == GRAY:
                    return True
                if color.get(nxt, BLACK) == WHITE and dfs(nxt):
                    return True
            color[node] = BLACK
            return False

        return any(color[n] == WHITE and dfs(n) for n in graph)


# ────────────────────────────────────────────────────────────────────
# Acceptance criteria
# ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AcceptanceCheck:
    """
    One rule the Critic uses to decide whether a deliverable is acceptable.

    `kind` selects a builtin verifier; `params` is its config.

    Builtin kinds:
        word_count_delta   — |new - orig| / orig must lie in `range_pct`
        no_new_facts       — semantic similarity above `min_score` (LLM-based)
        reading_grade_drop — Flesch-Kincaid grade must drop by at least `min_drop`
        contains_no        — output must NOT contain any of `forbidden_substrings`
        contains_all       — output must contain every `required_substrings`
        max_paragraphs     — paragraph count <= `limit`
        min_paragraphs     — paragraph count >= `limit`

    Unknown kinds are tolerated — the Critic logs and skips them rather than failing
    the whole job, so old YAML stays forward-compatible with future kinds.
    """

    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    blocking: bool = True   # if False — soft warning, still ship


@dataclass
class AcceptanceResult:
    """Result of running one AcceptanceCheck against a deliverable."""

    kind: str
    passed: bool
    message: str = ""
    metric: float | None = None
    blocking: bool = True


# ────────────────────────────────────────────────────────────────────
# Profession
# ────────────────────────────────────────────────────────────────────

@dataclass
class Profession:
    """
    A bundle of capabilities, a workflow template, and acceptance criteria
    defining one freelance role the agent can play.
    """

    id: str
    name: str
    cluster: str
    language: str                          # "en" | "ru" | "ru,en" | ...
    required_capabilities: list[str]
    system_prompt: str
    workflow: Workflow
    acceptance_criteria: list[AcceptanceCheck]
    price_range_usd: tuple[float, float]   # (min, max)
    autonomy_level_required: int = 2       # 0..5
    risk_class: str = "low"                # low | medium | high

    # Optional metadata
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.risk_class not in {"low", "medium", "high"}:
            logger.warning(
                "[Profession %s] unknown risk_class '%s' — defaulting to 'medium'",
                self.id, self.risk_class,
            )
            self.risk_class = "medium"
        if not (0 <= self.autonomy_level_required <= 5):
            raise ValueError(
                f"Profession '{self.id}': autonomy_level_required must be 0..5, "
                f"got {self.autonomy_level_required}"
            )

    def can_be_served_by(self, available_capabilities: set[str]) -> tuple[bool, list[str]]:
        """
        Returns (ok, missing). `ok` is True iff every required_capability is in
        `available_capabilities`. `missing` is the shortfall.
        """
        missing = [c for c in self.required_capabilities if c not in available_capabilities]
        return (not missing), missing

    def matches_brief(self, brief: str) -> float:
        """
        Naive keyword-overlap score in [0, 1] between the brief and this
        profession's tags/name. Real matching is delegated to the LLM via
        SkillRegistry.match() — this is just a cheap pre-filter.
        """
        if not brief:
            return 0.0
        words = {w.strip(".,!?:;\"'()[]{}").lower()
                 for w in brief.split() if len(w) > 2}
        if not words:
            return 0.0
        targets = {self.name.lower(), self.cluster.lower()} | {t.lower() for t in self.tags}
        # split multi-word tags / names
        target_words: set[str] = set()
        for t in targets:
            for w in t.split():
                if len(w) > 2:
                    target_words.add(w.strip(".,!?:;\"'()[]{}").lower())
        if not target_words:
            return 0.0
        overlap = words & target_words
        return len(overlap) / len(target_words)
