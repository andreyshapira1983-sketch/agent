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
    """Deterministic first-pass role classifier.

    Scoring rules
    -------------
    * Anchor terms (explicit operator commands like ``:repair``, ``:learn``)
      and long, specific multi-word phrases trigger a role on a **single**
      match — they cannot appear by accident in unrelated text.
    * Short, ambiguous terms (``ошибки``, ``diff``, ``source`` …) are
      "soft" signals: they count toward the score for a role but require
      **at least 2 soft matches** before the role is selected.
    * Multiple roles may score > 0; the one with the highest score wins.
      Priority order (insertion order below) breaks ties.
    """

    def route(self, text: str) -> RoleContext:
        lowered = (text or "").casefold()

        # ------------------------------------------------------------------
        # Stage 1: Anchor signals — single match is sufficient.
        # These are either explicit CLI commands (`:repair`) or very long,
        # domain-specific phrases that cannot appear in casual conversation.
        # ------------------------------------------------------------------
        if _has_any(lowered, _REPAIR_ANCHORS):
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
                reasons=("repair anchor",),
            )

        if _has_any(lowered, _LEARNING_ANCHORS):
            return _context(
                role="learning",
                tone="technical",
                output_style="plan",
                scopes=("learning", "architecture", "sources", "tools", "memory"),
                tags=(
                    "learning", "architecture", "source-backed", "knowledge",
                    "fact", "project", "tool", "tools", "memory",
                ),
                reasons=("learning anchor",),
            )

        # PROGRAMMING_READINESS and OPERATOR_SELF consist only of long, unique
        # phrases — single match is unambiguous.
        if _has_any(lowered, _PROGRAMMING_READINESS_TERMS):
            return _context(
                role="operator_chat",
                tone="human",
                output_style="conversation",
                scopes=("operator", "project", "code", "tests", "safety"),
                tags=(
                    "preference", "user-approved", "project", "decision",
                    "insight", "fact", "code", "tests", "safety",
                ),
                reasons=("programming readiness terms",),
            )

        if _has_any(lowered, _OPERATOR_SELF_TERMS):
            return _context(
                role="operator_chat",
                tone="human",
                output_style="conversation",
                scopes=("operator", "project", "runtime", "budget", "safety"),
                tags=(
                    "preference", "user-approved", "project", "decision",
                    "insight", "fact", "safety",
                ),
                reasons=("operator self/status terms",),
            )

        # ------------------------------------------------------------------
        # Stage 2: Score-based selection.
        # Compute how many soft terms match for each role, then pick the
        # winner. The winner must score >= 2 to be selected; a score of 1
        # on a generic word (e.g. "ошибки" in a history question) is not
        # enough to override the default operator_chat role.
        # ------------------------------------------------------------------
        _SCORE_MIN = 2

        role_scores: list[tuple[int, str]] = [
            (_count_hits(lowered, _REPAIR_TERMS),      "repair"),
            (_count_hits(lowered, _LEARNING_TERMS),    "learning"),
            (_count_hits(lowered, _PROGRAMMER_TERMS),  "programmer"),
            (_count_hits(lowered, _REPORT_TERMS),      "technical_report"),
            (_count_hits(lowered, _RESEARCH_TERMS),    "researcher"),
        ]
        best_score, best_role = max(role_scores, key=lambda x: x[0])

        if best_score >= _SCORE_MIN:
            if best_role == "repair":
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
                    reasons=(f"repair terms score={best_score}",),
                )
            if best_role == "learning":
                return _context(
                    role="learning",
                    tone="technical",
                    output_style="plan",
                    scopes=("learning", "architecture", "sources", "tools", "memory"),
                    tags=(
                        "learning", "architecture", "source-backed", "knowledge",
                        "fact", "project", "tool", "tools", "memory",
                    ),
                    reasons=(f"learning terms score={best_score}",),
                )
            if best_role == "programmer":
                return _context(
                    role="programmer",
                    tone="technical",
                    output_style="diff",
                    scopes=("code", "tests", "tools", "architecture"),
                    tags=(
                        "code", "tests", "tool", "tools", "fact", "knowledge",
                        "source-backed", "project", "repair", "self-repair",
                    ),
                    reasons=(f"programming terms score={best_score}",),
                )
            if best_role == "technical_report":
                return _context(
                    role="technical_report",
                    tone="audit",
                    output_style="report",
                    scopes=("architecture", "evidence", "audit", "project"),
                    tags=(
                        "architecture", "evidence", "audit", "fact",
                        "knowledge", "source-backed", "project", "decision",
                    ),
                    reasons=(f"report/audit terms score={best_score}",),
                )
            if best_role == "researcher":
                return _context(
                    role="researcher",
                    tone="technical",
                    output_style="report",
                    scopes=("sources", "evidence", "web", "research"),
                    tags=("source-backed", "knowledge", "fact", "research", "web", "project"),
                    reasons=(f"research terms score={best_score}",),
                )

        # ------------------------------------------------------------------
        # Stage 3: Default — generic question, operator conversation.
        # ------------------------------------------------------------------
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


def _count_hits(text: str, terms: tuple[str, ...]) -> int:
    """Return the number of distinct terms that appear in *text*."""
    return sum(1 for term in terms if term in text)


# Anchor signals — a single match is sufficient (explicit CLI commands).
_REPAIR_ANCHORS = (":repair", ":propose-repair")
_LEARNING_ANCHORS = (":learn",)

# Soft signals — ambiguous words that need score >= 2 to win.
_REPAIR_TERMS = (
    "self-repair", "саморемонт",
    "почини", "исправь", "сломал", "сломалось", "ошибка", "ошибки",
    "failing", "failed test", "rollback", "diff", "diagnose",
)

_LEARNING_TERMS = (
    "learn", "learning", "ingest", "изучи", "изучать",
    "обуч", "самообуч", "запомн", "knowledge", "source registry",
)

_PROGRAMMER_TERMS = (
    "код", "программ", "python", "pytest", "tests", "test_",
    "core/", "tools/", "runtime/", ".py", "function", "class ",
    "рефактор", "модуль", "файл",
)

_OPERATOR_SELF_TERMS = (
    "себя",
    "своей системе",
    "свои возможности",
    "что не готово",
    "сейчас не готово",
    "что у тебя сейчас не готово",
    "слабое место",
    "самопроверка",
    "безопасная проверка",
    "следующий безопасный тест",
    "operator status",
    "safe self-check",
    "current gaps",
)

_PROGRAMMING_READINESS_TERMS = (
    "готов к безопасной программной задаче",
    "готов к программной задаче",
    "готовность к программной задаче",
    "готов к задаче по коду",
    "coding readiness",
    "programming readiness",
    "safe coding task",
    "safe programming task",
)

_REPORT_TERMS = (
    "отчёт", "отчет", "audit", "review", "проверь", "проверка",
    "claims", "архитектур", "верифиц", "source", "evidence",
)

_RESEARCH_TERMS = (
    "найди", "поищи", "источник", "источники", "документац",
    "web", "latest", "current", "сейчас", "последн", "realtime",
)
