"""
brain/skills/registry.py — SkillRegistry.

Loads Profession YAML files from a directory, validates them, and lets the
Brain ask "which profession matches this brief?" and "can I actually serve it
with my current tools?".

Profession YAML schema (see also professions/text_editor.yaml):

    id: text_editor                       # unique snake_case
    name: Text Editor (English)
    cluster: text
    language: en
    description: optional human-readable summary
    tags: [edit, proofread, rewrite, copyediting]
    required_capabilities:
      - read_docx
      - llm_text_rewrite
      - write_docx
    system_prompt: |
      Multi-line system prompt the Brain uses for this profession.
    workflow:
      - id: read
        description: Read the incoming document
        action: tool_call
        tool: docx_reader
        params:
          action: extract_text
      - id: edit
        description: Improve the text
        action: llm_rewrite
        depends_on: [read]
      - id: write
        description: Write the edited result back to DOCX
        action: tool_call
        tool: docx_writer
        depends_on: [edit]
    acceptance_criteria:
      - kind: word_count_delta
        params: { range_pct: [-0.20, 0.10] }
      - kind: no_new_facts
        params: { min_score: 0.85 }
    price_range_usd: [3, 25]
    autonomy_level_required: 2
    risk_class: low
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import (
    AcceptanceCheck,
    Capability,
    Profession,
    Workflow,
    WorkflowStep,
)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# Errors
# ────────────────────────────────────────────────────────────────────

class ProfessionLoadError(ValueError):
    """Raised when a profession YAML cannot be parsed or fails validation."""


# ────────────────────────────────────────────────────────────────────
# Registry
# ────────────────────────────────────────────────────────────────────

@dataclass
class MatchResult:
    """One ranked candidate profession for a brief."""
    profession: Profession
    score: float
    can_serve: bool
    missing_capabilities: list[str]


class SkillRegistry:
    """
    Central catalogue of professions and capabilities the agent can fill.

    Construction is cheap — just `SkillRegistry()`. Use `register_capability`
    or `register_tool_capability` to declare what the agent can do, then
    `load_directory(path)` to bulk-load profession YAMLs.
    """

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}
        self._professions: dict[str, Profession] = {}

    # ──────────────────────────────────────────────────────────────────
    # Capabilities
    # ──────────────────────────────────────────────────────────────────

    def register_capability(self, capability: Capability) -> None:
        if capability.id in self._capabilities:
            logger.warning("[SkillRegistry] overwriting capability '%s'", capability.id)
        self._capabilities[capability.id] = capability

    def register_tool_capability(
        self,
        capability_id: str,
        tool_name: str,
        description: str = "",
    ) -> None:
        """Shortcut: declare a capability that maps to a registered tool."""
        self.register_capability(Capability(
            id=capability_id,
            description=description or f"Backed by tool '{tool_name}'",
            tool_name=tool_name,
        ))

    def capabilities(self) -> list[Capability]:
        return list(self._capabilities.values())

    def capability_ids(self) -> set[str]:
        return set(self._capabilities.keys())

    def has_capability(self, capability_id: str) -> bool:
        return capability_id in self._capabilities

    # ──────────────────────────────────────────────────────────────────
    # Professions
    # ──────────────────────────────────────────────────────────────────

    def register_profession(self, profession: Profession) -> None:
        if profession.id in self._professions:
            logger.warning("[SkillRegistry] overwriting profession '%s'", profession.id)
        self._professions[profession.id] = profession
        logger.info(
            "[SkillRegistry] registered profession '%s' (%s, %s)",
            profession.id, profession.cluster, profession.language,
        )

    def load_file(self, path: Path | str) -> Profession:
        """Load a single profession YAML. Raises ProfessionLoadError on issues."""
        p = Path(path)
        if not p.exists():
            raise ProfessionLoadError(f"Profession YAML not found: {p}")
        try:
            with p.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ProfessionLoadError(f"YAML parse error in {p}: {exc}") from exc

        if not isinstance(data, dict):
            raise ProfessionLoadError(f"Top-level YAML in {p} must be a mapping")

        profession = _profession_from_dict(data, source=p)
        self.register_profession(profession)
        return profession

    def load_directory(self, path: Path | str) -> int:
        """Load every *.yaml / *.yml from `path` (non-recursive). Returns count."""
        d = Path(path)
        if not d.exists():
            logger.warning("[SkillRegistry] directory not found: %s", d)
            return 0
        loaded = 0
        for entry in sorted(d.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in {".yaml", ".yml"}:
                continue
            try:
                self.load_file(entry)
                loaded += 1
            except ProfessionLoadError as exc:
                logger.error("[SkillRegistry] failed to load %s: %s", entry, exc)
        logger.info("[SkillRegistry] loaded %d profession(s) from %s", loaded, d)
        return loaded

    def get(self, profession_id: str) -> Profession:
        if profession_id not in self._professions:
            raise KeyError(
                f"Profession '{profession_id}' not registered. "
                f"Known: {sorted(self._professions)}"
            )
        return self._professions[profession_id]

    def professions(self) -> list[Profession]:
        return list(self._professions.values())

    def profession_ids(self) -> list[str]:
        return sorted(self._professions.keys())

    # ──────────────────────────────────────────────────────────────────
    # Matching
    # ──────────────────────────────────────────────────────────────────

    def can_serve(self, profession_id: str) -> tuple[bool, list[str]]:
        prof = self.get(profession_id)
        return prof.can_be_served_by(self.capability_ids())

    def match(
        self,
        brief: str,
        language: str | None = None,
        top_k: int = 3,
    ) -> list[MatchResult]:
        """
        Rank candidate professions for a brief.

        Score is `profession.matches_brief(brief)` (keyword overlap).
        Real semantic matching (LLM-based) should layer on top — call this
        first to pre-filter the candidate set cheaply, then ask the LLM to
        pick the winner.
        """
        candidates: list[MatchResult] = []
        avail = self.capability_ids()
        for prof in self._professions.values():
            if language and language not in prof.language:
                continue
            score = prof.matches_brief(brief)
            can, missing = prof.can_be_served_by(avail)
            candidates.append(MatchResult(
                profession=prof,
                score=score,
                can_serve=can,
                missing_capabilities=missing,
            ))
        candidates.sort(key=lambda c: (c.can_serve, c.score), reverse=True)
        return candidates[:top_k]

    def __len__(self) -> int:
        return len(self._professions)

    def __repr__(self) -> str:
        return (
            f"SkillRegistry(professions={self.profession_ids()}, "
            f"capabilities={sorted(self.capability_ids())})"
        )


# ────────────────────────────────────────────────────────────────────
# YAML → dataclass converters
# ────────────────────────────────────────────────────────────────────

_REQUIRED_FIELDS = {
    "id", "name", "cluster", "language",
    "required_capabilities", "system_prompt",
    "workflow", "acceptance_criteria", "price_range_usd",
}


def _profession_from_dict(data: dict[str, Any], source: Path | None = None) -> Profession:
    """Convert a parsed YAML dict to a Profession dataclass with validation."""
    where = f" (in {source})" if source else ""

    missing = _REQUIRED_FIELDS - data.keys()
    if missing:
        raise ProfessionLoadError(
            f"Missing required field(s) {sorted(missing)}{where}"
        )

    try:
        price = tuple(data["price_range_usd"])
        if len(price) != 2 or any(not isinstance(x, (int, float)) for x in price):
            raise ValueError
        price_range = (float(price[0]), float(price[1]))
    except (TypeError, ValueError) as exc:
        raise ProfessionLoadError(
            f"price_range_usd must be [min, max] numbers{where}"
        ) from exc

    workflow = _workflow_from_list(data["workflow"], source=source)
    errors = workflow.validate()
    if errors:
        raise ProfessionLoadError(
            f"Workflow validation failed{where}: {'; '.join(errors)}"
        )

    acceptance = _acceptance_from_list(data.get("acceptance_criteria", []))

    required_caps = data["required_capabilities"]
    if not isinstance(required_caps, list) or not all(isinstance(c, str) for c in required_caps):
        raise ProfessionLoadError(
            f"required_capabilities must be a list of strings{where}"
        )

    try:
        return Profession(
            id=str(data["id"]),
            name=str(data["name"]),
            cluster=str(data["cluster"]),
            language=str(data["language"]),
            required_capabilities=list(required_caps),
            system_prompt=str(data["system_prompt"]),
            workflow=workflow,
            acceptance_criteria=acceptance,
            price_range_usd=price_range,
            autonomy_level_required=int(data.get("autonomy_level_required", 2)),
            risk_class=str(data.get("risk_class", "low")),
            description=str(data.get("description", "")),
            tags=[str(t) for t in (data.get("tags") or [])],
        )
    except ValueError as exc:
        raise ProfessionLoadError(f"Profession build failed{where}: {exc}") from exc


def _workflow_from_list(items: Any, source: Path | None = None) -> Workflow:
    where = f" (in {source})" if source else ""
    if not isinstance(items, list) or not items:
        raise ProfessionLoadError(f"workflow must be a non-empty list{where}")
    steps: list[WorkflowStep] = []
    for raw in items:
        if not isinstance(raw, dict):
            raise ProfessionLoadError(f"workflow step must be a mapping{where}")
        sid = raw.get("id")
        if not sid or not isinstance(sid, str):
            raise ProfessionLoadError(f"workflow step missing 'id'{where}")
        action = raw.get("action")
        if not action or not isinstance(action, str):
            raise ProfessionLoadError(
                f"workflow step '{sid}' missing 'action'{where}"
            )
        steps.append(WorkflowStep(
            id=sid,
            description=str(raw.get("description", sid)),
            action=action,
            tool=raw.get("tool"),
            params=dict(raw.get("params") or {}),
            depends_on=[str(d) for d in (raw.get("depends_on") or [])],
        ))
    return Workflow(steps=steps)


def _acceptance_from_list(items: Any) -> list[AcceptanceCheck]:
    if not items:
        return []
    if not isinstance(items, list):
        raise ProfessionLoadError("acceptance_criteria must be a list")
    out: list[AcceptanceCheck] = []
    for raw in items:
        if not isinstance(raw, dict):
            raise ProfessionLoadError("acceptance_criteria item must be a mapping")
        kind = raw.get("kind")
        if not kind or not isinstance(kind, str):
            raise ProfessionLoadError("acceptance_criteria item missing 'kind'")
        out.append(AcceptanceCheck(
            kind=kind,
            params=dict(raw.get("params") or {}),
            blocking=bool(raw.get("blocking", True)),
        ))
    return out
