"""
brain/learning.py — Earned-knowledge extraction loop.

After a Job finishes, `learn_from_outcome` asks the LLM to distill the
transient artefacts (source text, deliverable, verifier report) into a
small set of durable, language-agnostic *learnings*:

    - Terminology pairs that the editor preserved   (✔ success path)
    - Stylistic preferences inferred from edits      (✔ success path)
    - Anti-patterns the verifier rejected            (✗ failure path)

These are then stored in SemanticMemory under namespaces:
    prof:<profession_id>                # success-derived knowledge
    antipatterns:<profession_id>        # failure-derived

This is the formalisation of the manual `learn_fact(...)` calls we used
in the live simulation: the agent grows a per-profession knowledge slice
just by doing the work and observing what passed acceptance.

Robustness
──────────
- Never raises into the WorkflowRunner. A failure to extract returns 0
  facts and is logged. Learning is opportunistic.
- The LLM is asked for STRICT JSON; malformed replies fall back to
  parsing line-by-line, and otherwise log + return empty.
- Token cap on prompts: we truncate source/deliverable when they exceed
  `MAX_LEARN_INPUT_CHARS`, keeping both head and tail.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .interfaces.llm_interface import LLMInterface
    from .skills.job import Job
    from .skills.knowledge import KnowledgeBase
    from .skills.workflow_runner import JobOutcome

logger = logging.getLogger(__name__)

MAX_LEARN_INPUT_CHARS = 4_000


# ════════════════════════════════════════════════════════════════════
# Records
# ════════════════════════════════════════════════════════════════════

@dataclass
class LearningRecord:
    """One distilled, durable fact extracted from a finished Job."""

    profession_id: str
    namespace:     str          # "prof:<id>" or "antipatterns:<id>"
    text:          str
    source:        str          # "success" | "failure"
    fact_id:       str = ""     # set after SemanticMemory.store_fact


@dataclass
class LearningReport:
    """What `learn_from_outcome` produced for one Job."""

    job_id:        str
    profession_id: str
    success:       bool
    extracted:     list[LearningRecord] = field(default_factory=list)
    notes:         list[str] = field(default_factory=list)

    def fact_count(self) -> int:
        return len(self.extracted)


# ════════════════════════════════════════════════════════════════════
# Core extractor
# ════════════════════════════════════════════════════════════════════

_SYS_SUCCESS = (
    "You are a knowledge distiller for an autonomous freelance editing agent. "
    "Compare ORIGINAL and DELIVERABLE. Extract at most 10 short, durable, "
    "actionable facts the agent should remember to do similar work better next time. "
    "Examples: terminology preservation pairs (en→ru), tone preferences, "
    "formatting rules. Output STRICT JSON array of strings. No prose."
)

_SYS_FAILURE = (
    "You are an anti-pattern miner for an autonomous freelance agent. "
    "The deliverable below FAILED acceptance checks. Extract at most 10 short "
    "rules describing what NOT to do next time so future deliverables pass. "
    "Output STRICT JSON array of strings. No prose."
)


def learn_from_outcome(
    job: "Job",
    outcome: "JobOutcome",
    *,
    llm: "LLMInterface",
    semantic_memory,                    # type: ignore[no-untyped-def]
    knowledge_base: "KnowledgeBase | None" = None,
    max_facts: int = 10,
) -> LearningReport:
    """Distill a finished Job into durable facts and store them.

    Args:
        job:             The Job (gives us context — brief, client, source).
        outcome:         WorkflowRunner output (gives us the deliverable and
                         verifier verdict).
        llm:             Same LLM interface the Brain uses.
        semantic_memory: SemanticMemory instance to write facts into.
        knowledge_base:  Optional KnowledgeBase. When passed, the loader
                         also indexes the stored facts so they survive
                         `KnowledgeBase.forget_profession()` reloads.
        max_facts:       Hard cap on how many facts to store.

    Returns:
        A LearningReport. `fact_count()` is the count of newly stored facts.
    """
    report = LearningReport(
        job_id=job.id,
        profession_id=outcome.profession_id,
        success=(outcome.final_status.value == "delivered"),
    )
    if not outcome.profession_id:
        report.notes.append("no profession_id on outcome — skipping")
        return report

    original = _trim_for_prompt(_source_text(outcome, job))
    deliverable = _trim_for_prompt(outcome.deliverable_text)

    if not deliverable.strip():
        report.notes.append("no deliverable text — skipping")
        return report

    sys_prompt = _SYS_SUCCESS if report.success else _SYS_FAILURE
    user_prompt = (
        f"PROFESSION: {outcome.profession_id}\n"
        f"BRIEF: {job.brief}\n\n"
        f"ORIGINAL:\n{original or '(not available)'}\n\n"
        f"DELIVERABLE:\n{deliverable}\n"
    )
    if outcome.verifier_report is not None:
        user_prompt += f"\nVERIFIER:\n{outcome.verifier_report.summary}\n"

    try:
        reply = llm.call({
            "input":      user_prompt,
            "goals":      [{"goal": sys_prompt, "priority": 1}],
            "history":    [],
            "facts":      [],
            "session_id": f"learn:{job.id}",
        })
    except Exception as exc:  # noqa: BLE001 — learning never fails the agent
        report.notes.append(f"LLM error: {type(exc).__name__}: {exc}")
        logger.exception("[learn_from_outcome] LLM call raised")
        return report

    text = ""
    if isinstance(reply, dict):
        text = str(reply.get("content") or reply.get("text") or "")
    facts = _parse_facts(text, max_facts=max_facts)
    if not facts:
        report.notes.append("LLM produced no parseable facts")
        return report

    namespace = (
        f"prof:{outcome.profession_id}"
        if report.success
        else f"antipatterns:{outcome.profession_id}"
    )

    for fact_text in facts:
        try:
            fact_id = semantic_memory.store_fact(
                text=fact_text,
                metadata={
                    "namespace":     namespace,
                    "profession":    outcome.profession_id,
                    "source":        "learn_from_outcome",
                    "outcome":       "success" if report.success else "failure",
                    "job_id":        job.id,
                },
            )
        except Exception as exc:  # noqa: BLE001
            report.notes.append(f"store_fact failed: {exc}")
            continue
        record = LearningRecord(
            profession_id=outcome.profession_id,
            namespace=namespace,
            text=fact_text,
            source="success" if report.success else "failure",
            fact_id=fact_id,
        )
        report.extracted.append(record)
        if knowledge_base is not None:
            knowledge_base._index.add(outcome.profession_id, fact_id)  # noqa: SLF001

    logger.info(
        "[learn_from_outcome] job=%s profession=%s success=%s facts=%d",
        job.id, outcome.profession_id, report.success, len(report.extracted),
    )
    return report


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════

def _source_text(outcome, job) -> str:
    """Reconstruct the source text from outcome.step_records when available."""
    for record in outcome.step_records:
        if record.action == "tool_call" and record.tool == "docx_reader" and record.success:
            if isinstance(record.output, str):
                return record.output
            if isinstance(record.output, dict):
                txt = record.output.get("text")
                if isinstance(txt, str):
                    return txt
    return ""


def _trim_for_prompt(text: str) -> str:
    if len(text) <= MAX_LEARN_INPUT_CHARS:
        return text
    head = text[: MAX_LEARN_INPUT_CHARS // 2]
    tail = text[-MAX_LEARN_INPUT_CHARS // 2 :]
    return f"{head}\n\n[...truncated for prompt...]\n\n{tail}"


def _parse_facts(text: str, *, max_facts: int) -> list[str]:
    """Try strict JSON first; fall back to line-extraction."""
    text = text.strip()
    if not text:
        return []
    # Strip code fences if present
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            facts = [str(x).strip() for x in parsed if str(x).strip()]
            return _dedup_keep_order(facts)[:max_facts]
        # A JSON object that's NOT a list means the LLM ignored the schema.
        # Don't pretend it's a fact.
        return []
    except json.JSONDecodeError:
        pass
    # Line-fallback: bullets, numbered, or plain lines.
    lines = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-•*").strip()
        line = re.sub(r"^\d+[.)]\s*", "", line)
        if line and len(line) > 3 and not line.startswith("{"):
            lines.append(line)
    return _dedup_keep_order(lines)[:max_facts]


def _dedup_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        key = x.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
    return out
