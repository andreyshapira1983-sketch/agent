"""Workflow templates after Agent Workflow Memory (Wang et al., arXiv 2409.07429), stored separately from procedural memory.

A model builds a template from SEVERAL successful experiences of one group (a repeated sub-task, not a whole task); variable parts
(paths, queries, numbers) become `{variables}`; a template is a description plus steps (why, which tool). Groups are work kinds
(core/work_kinds.work_kinds) and the prompt gets the templates of the current question's kind. Procedural-memory keys are load-bearing,
hence a separate store.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.ids import new_id
from core.state_integrity import (
    read_state_jsonl_unlocked,
    rewrite_state_jsonl_unlocked,
    state_file_lock,
)
from core.work_kinds import work_kinds

FILE_NAME = "workflow_memory.jsonl"
#: Сколько опытов группы показывать модели: больше — дороже и не лучше.
MAX_EXPERIENCES = 12
#: Шаблонов рода в одной подсказке.
MAX_IN_PROMPT = 3
#: Литерал опыта, оставшийся в шаге, — шаблон не абстрагирован (путь, адрес).
#: Ещё пути Windows и UNC, адреса без схемы (проверка 17.11, средняя).
_LITERAL_RE = re.compile(
    r"(?:[\w.-]+/)+[\w.-]+\.[A-Za-z]\w*|\b[a-z][a-z0-9+.-]*://\S+|\bwww\.\S+"
    r"|\b[A-Za-z]:[\\/]\S*|\\\\[\w.$-]+\\\S*|(?:[\w.-]+\\)+[\w.-]+\.[A-Za-z]\w*",
    re.IGNORECASE)

#: A reusable work routine has no business talking about the agent's own
#: prompt or instructions (narrow on purpose: «новый правильный порядок» is fine).
_MENTIONS_PROMPT_RE = re.compile(
    r"system\s+prompt|your\s+(?:own\s+)?instructions|системн\w*\s+(?:промпт|подсказк)\w*"
    r"|(?:твои|свои|ваши)\s+инструкци\w*",
    re.IGNORECASE)

INDUCE_SYSTEM = """You extract reusable WORKFLOWS from an AI agent's past successful work.
You get several past experiences of the same kind. Find the common, repeatedly used
sub-routines (a part of the work that recurs across experiences, not one whole task).
Rules:
- Each workflow = a short description of what it achieves + ordered steps.
- Each step: why it is done, then the tool it uses (one of the tools seen in the experiences).
- Replace every non-fixed value (file paths, URLs, search queries, names, numbers)
  with a descriptive variable in curly braces, e.g. {source_file}, {search_query}.
- Only include a workflow that occurs in at least two experiences.
Return JSON only: {"workflows": [{"description": "...", "steps": ["why -> tool", ...]}]}"""


@dataclass(frozen=True)
class WorkflowRecord:
    kind: str
    description: str
    steps: tuple[str, ...]
    source_count: int = 0
    id: str = field(default_factory=lambda: new_id("wf"))
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "description": self.description,
                "steps": list(self.steps), "source_count": self.source_count,
                "created_at": self.created_at}

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> WorkflowRecord:
        steps = row.get("steps") or ()
        if isinstance(steps, str):
            # A line is one step, not a step per letter.
            steps = (steps,)
        return cls(kind=str(row.get("kind") or ""), description=str(row.get("description") or ""),
                   steps=tuple(str(s) for s in steps),
                   source_count=int(row.get("source_count") or 0),
                   id=str(row.get("id") or new_id("wf")), created_at=str(row.get("created_at") or ""))


class WorkflowMemoryStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> list[WorkflowRecord]:
        if not self.path.exists():
            return []
        with state_file_lock(self.path):
            rows = read_state_jsonl_unlocked(self.path)
        out: list[WorkflowRecord] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            try:
                out.append(WorkflowRecord.from_dict(r))
            except (TypeError, ValueError):
                # One broken line must not take down the whole hint.
                continue
        return out

    def replace_kind(self, kind: str, records: list[WorkflowRecord]) -> None:
        """Templates of a kind are rebuilt whole: new output replaces the old."""
        with state_file_lock(self.path):
            rows = read_state_jsonl_unlocked(self.path) if self.path.exists() else []
            kept = [r for r in rows if isinstance(r, dict) and r.get("kind") != kind]
            rewrite_state_jsonl_unlocked(self.path, [*kept, *(r.to_dict() for r in records)])

    def for_question(self, question: str, limit: int = MAX_IN_PROMPT) -> list[WorkflowRecord]:
        kinds = work_kinds(question)
        if not kinds:
            return []
        return [r for r in self.load() if r.kind in kinds][:limit]


def experience_lines(episodes: list[Any], procedures: list[Any]) -> dict[str, list[str]]:
    """Successful experiences per work kind: a line "what was asked; with what; over what"."""
    from core.smart_memory import lesson_from_episode, procedure_credit_allowed

    groups: dict[str, list[str]] = {}

    def add(question: str, line: str) -> None:
        for kind in work_kinds(question):
            bucket = groups.setdefault(kind, [])
            if line and line not in bucket:
                bucket.append(line)

    for ep in episodes:
        if procedure_credit_allowed(ep):
            add(ep.question, lesson_from_episode(ep))
    for proc in procedures:
        # Only active ones: candidate and needs_review are unverified.
        if getattr(proc, "status", "") != "active":
            continue
        for question, lesson in zip(proc.source_questions, proc.lessons, strict=False):
            add(question, lesson)
    return groups


def unabstracted(steps: tuple[str, ...] | list[str]) -> list[str]:
    """Steps that still contain an experience literal (file path, address) instead of a variable."""
    return [s for s in steps if _LITERAL_RE.search(s)]


def _unsafe(texts: tuple[str, ...]) -> bool:
    """True when the guard blocks the text or it addresses the agent's instructions; a guard error is also a refusal."""
    try:
        # The SAME rule as the memory write gate: text that addresses the model's instructions, or two blocked guard categories at once.
        # The guard's single "blocked" plus a loose stem regex refused ordinary steps.
        from core.memory_policy import _memory_injection

        return any(_memory_injection(t) or _MENTIONS_PROMPT_RE.search(t) for t in texts)
    except Exception:  # noqa: BLE001 — fail-closed, reason above
        return True


def induce(kind: str, lines: list[str], llm: Any) -> list[WorkflowRecord]:
    """Templates of a kind after AWM; an unparsed answer or an unabstracted step is not a template."""
    if len(lines) < 2:
        return []
    shown = lines[-MAX_EXPERIENCES:]
    user = f"Kind of work: {kind}\nExperiences:\n" + "\n".join(f"{i + 1}. {x}" for i, x in enumerate(shown))
    raw = llm.complete(system=INDUCE_SYSTEM, user=user, max_tokens=1500, temperature=0.2, json_object=True)
    try:
        data = json.loads(str(raw))
    except ValueError:
        return []
    out: list[WorkflowRecord] = []
    for item in (data.get("workflows") if isinstance(data, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        raw_steps = item.get("steps") or ()
        if isinstance(raw_steps, str):
            # A line is one step, as in from_dict.
            raw_steps = (raw_steps,)
        elif not isinstance(raw_steps, (list, tuple)):
            continue
        steps = tuple(str(s).strip() for s in raw_steps if str(s).strip())
        description = str(item.get("description") or "").strip()
        # The description is checked like the steps.
        if (description and len(steps) >= 2 and not unabstracted((description, *steps))
                and not _unsafe((description, *steps))):
            out.append(WorkflowRecord(kind=kind, description=description, steps=steps[:8],
                                      source_count=len(shown)))
    return out


def induce_all(data_dir: Path | str, llm: Any, *, out: Path | str | None = None) -> dict[str, int]:
    """Build templates of all kinds from the experience in `data_dir`; returns counts per kind.

        `out` is where to write (default `data_dir/workflow_memory.jsonl`): a measurement builds into a separate file, leaving live memory alone.
    """
    from core.smart_memory import EpisodicMemoryStore, ProceduralMemoryStore

    data_dir = Path(data_dir)
    episodes = EpisodicMemoryStore(data_dir / "episodic_memory.jsonl").load()
    procedures = ProceduralMemoryStore(data_dir / "procedural_memory.jsonl").load()
    store = WorkflowMemoryStore(out or data_dir / FILE_NAME)
    made: dict[str, int] = {}
    for kind, lines in sorted(experience_lines(episodes, procedures).items()):
        records = induce(kind, lines, llm)
        if records:
            store.replace_kind(kind, records)
        made[kind] = len(records)
    return made


def format_workflows(records: list[WorkflowRecord]) -> str:
    """Prompt block: templates are a hint about work order, not a command."""
    if not records:
        return ""
    lines = ["<agent_workflows>",
             "Reusable ways this kind of work was done before; adapt them, fill the {variables}:"]
    for rec in records:
        lines.append(f"- {_inert(rec.description)}")
        lines.extend(f"    {i + 1}. {_inert(step)}" for i, step in enumerate(rec.steps))
    lines.append("</agent_workflows>")
    return "\n".join(lines)


def _inert(text: str) -> str:
    """Model text is data: without angle brackets and newlines it cannot close the <agent_workflows> block."""
    return " ".join(str(text).replace("<", "‹").replace(">", "›").split())
