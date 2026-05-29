"""Role / mode routing for the agent core.

The agent should not use the same posture for every task. A repair run, a
technical audit, a human chat, and a learning pass all need different memory
scopes and output expectations. This layer is deliberately deterministic: the
router is policy, not another LLM prompt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


AgentRole = Literal[
    "operator_chat",
    "technical_report",
    "programmer",
    "researcher",
    "learning",
    "repair",
]

OutputTone = Literal["human", "technical", "concise", "audit"]
OutputStyle = Literal["conversation", "report", "plan", "diff"]


@dataclass(frozen=True)
class RoleContext:
    role: AgentRole
    tone: OutputTone
    output_style: OutputStyle
    knowledge_scopes: tuple[str, ...]
    allowed_memory_types: tuple[str, ...]
    allowed_memory_tags: tuple[str, ...]
    reasons: tuple[str, ...] = ()

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "tone": self.tone,
            "output_style": self.output_style,
            "knowledge_scopes": list(self.knowledge_scopes),
            "allowed_memory_types": list(self.allowed_memory_types),
            "allowed_memory_tags": list(self.allowed_memory_tags),
            "reasons": list(self.reasons),
        }

    def to_prompt_block(self) -> str:
        return (
            "<role_context>\n"
            f"role: {self.role}\n"
            f"tone: {self.tone}\n"
            f"output_style: {self.output_style}\n"
            f"knowledge_scopes: {', '.join(self.knowledge_scopes)}\n"
            "</role_context>"
        )


class RoleRouter:
    """Deterministic first-pass role classifier."""

    def route(self, text: str) -> RoleContext:
        lowered = (text or "").casefold()
        hits: list[str] = []

        if _has_any(lowered, _REPAIR_TERMS):
            hits.append("repair terms")
            return _context(
                role="repair",
                tone="technical",
                output_style="plan",
                scopes=("self_repair", "tests", "tools", "code", "safety"),
                tags=(
                    "repair", "self-repair", "tests", "tool", "tools",
                    "code", "fact", "knowledge", "source-backed",
                    "project", "decision", "insight",
                ),
                reasons=tuple(hits),
            )

        if _has_any(lowered, _LEARNING_TERMS):
            hits.append("learning terms")
            return _context(
                role="learning",
                tone="technical",
                output_style="plan",
                scopes=("learning", "architecture", "sources", "tools", "memory"),
                tags=(
                    "learning", "architecture", "source-backed", "knowledge",
                    "fact", "project", "tool", "tools", "memory",
                ),
                reasons=tuple(hits),
            )

        if _has_any(lowered, _PROGRAMMER_TERMS):
            hits.append("programming terms")
            return _context(
                role="programmer",
                tone="technical",
                output_style="diff",
                scopes=("code", "tests", "tools", "architecture"),
                tags=(
                    "code", "tests", "tool", "tools", "fact", "knowledge",
                    "source-backed", "project", "repair", "self-repair",
                ),
                reasons=tuple(hits),
            )

        if _has_any(lowered, _REPORT_TERMS):
            hits.append("report/audit terms")
            return _context(
                role="technical_report",
                tone="audit",
                output_style="report",
                scopes=("architecture", "evidence", "audit", "project"),
                tags=(
                    "architecture", "evidence", "audit", "fact",
                    "knowledge", "source-backed", "project", "decision",
                ),
                reasons=tuple(hits),
            )

        if _has_any(lowered, _RESEARCH_TERMS):
            hits.append("research terms")
            return _context(
                role="researcher",
                tone="technical",
                output_style="report",
                scopes=("sources", "evidence", "web", "research"),
                tags=("source-backed", "knowledge", "fact", "research", "web", "project"),
                reasons=tuple(hits),
            )

        return _context(
            role="operator_chat",
            tone="human",
            output_style="conversation",
            scopes=("operator", "preference", "project"),
            tags=("preference", "user-approved", "project", "decision", "insight", "fact"),
            reasons=("default operator chat",),
        )


def _context(
    *,
    role: AgentRole,
    tone: OutputTone,
    output_style: OutputStyle,
    scopes: tuple[str, ...],
    tags: tuple[str, ...],
    reasons: tuple[str, ...],
) -> RoleContext:
    return RoleContext(
        role=role,
        tone=tone,
        output_style=output_style,
        knowledge_scopes=scopes,
        allowed_memory_types=("working", "semantic", "procedural", "episodic"),
        allowed_memory_tags=tags,
        reasons=reasons,
    )


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


_REPAIR_TERMS = (
    ":repair", ":propose-repair", "self-repair", "саморемонт",
    "почини", "исправь", "сломал", "сломалось", "ошибка", "ошибки",
    "failing", "failed test", "rollback", "diff", "diagnose",
)

_LEARNING_TERMS = (
    ":learn", "learn", "learning", "ingest", "изучи", "изучать",
    "обуч", "самообуч", "запомн", "knowledge", "source registry",
)

_PROGRAMMER_TERMS = (
    "код", "программ", "python", "pytest", "tests", "test_",
    "core/", "tools/", "runtime/", ".py", "function", "class ",
    "рефактор", "модуль", "файл",
)

_REPORT_TERMS = (
    "отчёт", "отчет", "audit", "review", "проверь", "проверка",
    "claims", "архитектур", "верифиц", "source", "evidence",
)

_RESEARCH_TERMS = (
    "найди", "поищи", "источник", "источники", "документац",
    "web", "latest", "current", "сейчас", "последн", "realtime",
)
