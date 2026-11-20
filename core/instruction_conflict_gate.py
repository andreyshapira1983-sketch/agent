"""Instruction conflict gate: rank sources by authority; on any conflict block every state-mutating action except reporting it."""
# The ranking only names the priority in the report; the agent never resolves a conflict itself. The module decides, callers enforce.
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal


AuthorityLevel = Literal[
    "operator",  # 1 owner of the system
    "task_contract",  # 2 what the task explicitly requires or forbids
    "test_expectation",  # 3 behaviour pinned by a green test
    "repo_invariant",  # 4 repository doctrine and invariants
    "local_convention",  # 5 style of surrounding code
    "advisor",  # 6 review, linter, convenience
]

# Lower number = higher authority. A lower level NEVER overrides a higher one.
AUTHORITY_RANK: dict[str, int] = {
    "operator": 1,
    "task_contract": 2,
    "test_expectation": 3,
    "repo_invariant": 4,
    "local_convention": 5,
    "advisor": 6,
}

_LEVEL_RU: dict[str, str] = {
    "operator": "оператор",
    "task_contract": "контракт задачи",
    "test_expectation": "тест",
    "repo_invariant": "инвариант репозитория",
    "local_convention": "конвенция кода",
    "advisor": "советчик (ревью/линтер)",
}

# Unknown levels rank weakest: an unrecognised label must never win.
_UNKNOWN_RANK = max(AUTHORITY_RANK.values()) + 1



GateMode = Literal["proceed", "blocked"]
AllowedAction = Literal["report_conflict"]

# The single action permitted while a conflict is open.
ALLOWED_WHILE_CONFLICTED: AllowedAction = "report_conflict"

# Everything that changes state; none may run while a conflict is open.
FORBIDDEN_WHILE_CONFLICTED: tuple[str, ...] = (
    "write_file",
    "create_file",
    "delete_file",
    "modify_test",
    "apply_patch",
    "create_module",
    "install_dependency",
    "git_add",
    "git_commit",
    "git_push",
)



def _normalize(text: str) -> str:
    """Collapse whitespace, lowercase, drop trailing punctuation; for comparison only, the original text is reported."""
    flat = " ".join(str(text or "").split()).lower()
    return re.sub(r"[.,;:!?\s]+$", "", flat)


@dataclass(frozen=True)
class Directive:
    """One requirement and who makes it; two directives conflict only when they share a subject and demand different things."""

    source_level: AuthorityLevel
    source_name: str
    subject: str
    demand: str
    locator: str = ""  # file:line / URL, optional
    quote: str = ""  # verbatim wording, optional but preferred

    @property
    def rank(self) -> int:
        return AUTHORITY_RANK.get(self.source_level, _UNKNOWN_RANK)

    @property
    def level_ru(self) -> str:
        return _LEVEL_RU.get(self.source_level, self.source_level)

    def cite(self) -> str:
        """One-line citation: level, source, verbatim demand."""
        where = f" ({self.locator})" if self.locator else ""
        said = self.quote.strip() or self.demand
        return f"[{self.rank}] {self.level_ru} — {self.source_name}{where}: «{said}»"

    def to_dict(self) -> dict[str, object]:
        return {
            "source_level": self.source_level,
            "source_name": self.source_name,
            "subject": self.subject,
            "demand": self.demand,
            "locator": self.locator,
            "quote": self.quote,
            "rank": self.rank,
        }



@dataclass(frozen=True)
class ConflictFinding:
    subject: str
    higher: Directive
    lower: Directive

    @property
    def same_level(self) -> bool:
        return self.higher.rank == self.lower.rank

    def priority_verdict(self) -> str:
        if self.same_level:
            return (
                f"оба источника на одном уровне ({self.higher.level_ru}) — "
                "автоматического приоритета нет, решает оператор"
            )
        return (
            f"выше по полномочиям: {self.higher.level_ru} "
            f"({self.higher.source_name}); "
            f"ниже: {self.lower.level_ru} ({self.lower.source_name})"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "higher": self.higher.to_dict(),
            "lower": self.lower.to_dict(),
            "same_level": self.same_level,
            "priority_verdict": self.priority_verdict(),
        }



RESOLUTION_STEPS: tuple[str, ...] = (
    "привести дословно оба требования и их источники",
    "спросить оператора, какой источник имеет силу в этом случае",
    ("если по существу прав нижний источник — оператор меняет верхний документ "
    "(спецификацию, контракт, тест), а не агент обходит его в коде"),
    ("только после письменного решения оператора снять блокировку и внести "
    "правку одним изменением"),
    "записать эпизод (инструкция → конфликт → решение) в процедурную память",
)



@dataclass(frozen=True)
class InstructionConflictOutcome:
    mode: GateMode
    findings: tuple[ConflictFinding, ...]
    allowed_action: AllowedAction | None
    forbidden_actions: tuple[str, ...]
    resolution_steps: tuple[str, ...]
    reason: str

    @property
    def is_blocked(self) -> bool:
        return self.mode == "blocked"

    def is_forbidden(self, action: str) -> bool:
        """True iff action must not run given this outcome; in proceed mode nothing is forbidden."""
        return self.mode == "blocked" and action in self.forbidden_actions

    def report(self) -> str:
        """The six-point operator report required by INSTRUCTION_AUTHORITY §4."""
        if self.mode != "blocked" or not self.findings:
            return ""

        subjects = ", ".join(
            dict.fromkeys(finding.subject for finding in self.findings)
        )
        lines = [
            "=== конфликт инструкций ===",
            f"1. Конфликт обнаружен. Предмет: {subjects}",
            "2. Источники:",
        ]
        for finding in self.findings:
            lines.append(f"   предмет «{finding.subject}»:")
            lines.append(f"     - {finding.higher.cite()}")
            lines.append(f"     - {finding.lower.cite()}")
        lines.append("3. Приоритет:")
        for finding in self.findings:
            lines.append(f"   - {finding.subject}: {finding.priority_verdict()}")
        lines.append(
            "4. Код и тесты не изменялись — гейт запретил: "
            + ", ".join(self.forbidden_actions)
        )
        lines.append("5. Процедура разрешения:")
        lines.extend(f"   {i}. {step}" for i, step in enumerate(
            self.resolution_steps, start=1
        ))
        lines.append(
            "6. Остановка: работа не продолжена сознательно. Продолжу только "
            "после решения оператора по пункту 5."
        )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "findings": [finding.to_dict() for finding in self.findings],
            "allowed_action": self.allowed_action,
            "forbidden_actions": list(self.forbidden_actions),
            "resolution_steps": list(self.resolution_steps),
            "reason": self.reason,
        }


_PROCEED = InstructionConflictOutcome(
    mode="proceed",
    findings=(),
    allowed_action=None,
    forbidden_actions=(),
    resolution_steps=(),
    reason="противоречий между требованиями не найдено",
)



def evaluate(directives: Iterable[Directive]) -> InstructionConflictOutcome:
    """Decide PROCEED vs BLOCKED: directives conflict when they share a subject and demand different things."""
    findings = _find_conflicts(directives)
    if not findings:
        return _PROCEED

    subjects = ", ".join(
        dict.fromkeys(finding.subject for finding in findings)
    )
    return InstructionConflictOutcome(
        mode="blocked",
        findings=findings,
        allowed_action=ALLOWED_WHILE_CONFLICTED,
        forbidden_actions=FORBIDDEN_WHILE_CONFLICTED,
        resolution_steps=RESOLUTION_STEPS,
        reason=f"несовместимые требования к одному предмету: {subjects}",
    )


def _find_conflicts(
    directives: Iterable[Directive],
) -> tuple[ConflictFinding, ...]:
    by_subject: dict[str, list[Directive]] = {}
    for directive in directives:
        subject_key = _normalize(directive.subject)
        if not subject_key:
            continue
        by_subject.setdefault(subject_key, []).append(directive)

    findings: list[ConflictFinding] = []
    for group in by_subject.values():
        if len(group) < 2:
            continue
        # Strongest authority first; ties keep input order for a reproducible result.
        ranked = sorted(group, key=lambda item: item.rank)
        strongest = ranked[0]
        strongest_demand = _normalize(strongest.demand)
        for other in ranked[1:]:
            if _normalize(other.demand) == strongest_demand:
                continue  # same requirement restated
            findings.append(ConflictFinding(
                subject=strongest.subject,
                higher=strongest,
                lower=other,
            ))
    return tuple(findings)


def reviewer_vs_contract(
    *,
    subject: str,
    contract_demand: str,
    reviewer_demand: str,
    contract_source: str = "постановка задачи",
    reviewer_source: str = "код-ревью",
) -> InstructionConflictOutcome:
    """Shortcut for the common shape: a reviewer contradicting the task."""
    return evaluate((
        Directive(
            source_level="task_contract",
            source_name=contract_source,
            subject=subject,
            demand=contract_demand,
        ),
        Directive(
            source_level="advisor",
            source_name=reviewer_source,
            subject=subject,
            demand=reviewer_demand,
        ),
    ))
