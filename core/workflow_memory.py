"""Шаблоны работы по Agent Workflow Memory (Wang et al., arXiv 2409.07429).

Зачем, замер 2026-09-25. Процедурная память (core/smart_memory.py) хранит
СЛЕДЫ: ключ — точная цепочка инструментов, поэтому 98 процедур дали 98 разных
ключей и ни одна похожая задача не легла к другой; шаги — «Run tool: file_read»
четыре раза, а «ситуация» — дословный старый вопрос. При этом процедуры шли в
подсказку в 95% ходов. Ключ старых процедур несущий (по нему решается, какая
процедура отработала), поэтому шаблоны — ОТДЕЛЬНОЕ хранилище рядом.

Как в AWM:
* шаблон строит модель из НЕСКОЛЬКИХ успешных опытов одной группы — общий
  повторяющийся кусок работы (подзадача, а не вся задача);
* изменчивое (пути, запросы, числа) заменяется переменными в фигурных скобках;
* шаблон — описание плюс шаги, у шага — зачем он и каким инструментом;
* группы — как «сайт» в AWM; у нас это род работы (core/work_kinds.work_kinds),
  и в подсказку идут шаблоны рода текущего вопроса.

Ограничения из той же статьи, которые надо помнить: жёсткая цепочка шагов
плохо переживает перемену среды посреди работы, и разница «модель против
правила» при построении невелика (45,1 против 43,4 успеха шагов на Mind2Web) —
главное подзадачи и переменные. Польза проверяется экзаменом, а не на глаз.
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
_LITERAL_RE = re.compile(r"(?:[\w.-]+/)+[\w.-]+\.\w+|https?://\S+")

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
        return cls(kind=str(row.get("kind") or ""), description=str(row.get("description") or ""),
                   steps=tuple(str(s) for s in row.get("steps") or ()),
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
        return [WorkflowRecord.from_dict(r) for r in rows if isinstance(r, dict)]

    def replace_kind(self, kind: str, records: list[WorkflowRecord]) -> None:
        """Шаблоны рода строятся заново целиком: новый вывод заменяет старый."""
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
    """Успешные опыты по родам работы: строка «что просили; чем; над чем»."""
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
        if getattr(proc, "status", "") == "obsolete":
            continue
        for question, lesson in zip(proc.source_questions, proc.lessons, strict=False):
            add(question, lesson)
    return groups


def unabstracted(steps: tuple[str, ...] | list[str]) -> list[str]:
    """Шаги, в которых остался литерал опыта (путь к файлу, адрес) вместо переменной."""
    return [s for s in steps if _LITERAL_RE.search(s)]


def induce(kind: str, lines: list[str], llm: Any) -> list[WorkflowRecord]:
    """Шаблоны рода по AWM; неразобранный ответ или неабстрагированный шаг — не шаблон."""
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
        steps = tuple(str(s).strip() for s in item.get("steps") or () if str(s).strip())
        description = str(item.get("description") or "").strip()
        if description and len(steps) >= 2 and not unabstracted(steps):
            out.append(WorkflowRecord(kind=kind, description=description, steps=steps[:8],
                                      source_count=len(shown)))
    return out


def induce_all(data_dir: Path | str, llm: Any, *, out: Path | str | None = None) -> dict[str, int]:
    """Построить шаблоны всех родов из опыта в `data_dir`; вернуть число по родам.

    `out` — куда писать (по умолчанию `data_dir/workflow_memory.jsonl`): замер
    строит шаблоны в отдельный файл, не трогая живую память.
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
    """Блок подсказки: шаблоны — подсказка к порядку работы, а не приказ."""
    if not records:
        return ""
    lines = ["<agent_workflows>",
             "Reusable ways this kind of work was done before; adapt them, fill the {variables}:"]
    for rec in records:
        lines.append(f"- {rec.description}")
        lines.extend(f"    {i + 1}. {step}" for i, step in enumerate(rec.steps))
    lines.append("</agent_workflows>")
    return "\n".join(lines)
