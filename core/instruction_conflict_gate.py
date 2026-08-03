"""Instruction Conflict Gate — турникет между уверенным приказом и ``git add``.

The Clarification Gate (``core.clarification_gate``) stops the agent when it
does not understand *what* to build. This gate stops it for the other reason:
it understands both instructions perfectly well, and they contradict each other.

The failure it prevents (operator's words):

    Уверенность формулировки не создаёт полномочий.

A reviewer says "sort by name"; the task contract says the order must be
stable. Both sentences are clear, both sound authoritative, and an agent with
no authority ranking obeys whichever arrived last — usually the reviewer —
and silently breaks what the task required it to preserve.

So this module does two things:

* ranks instruction sources by authority (see ``docs/INSTRUCTION_AUTHORITY.md``);
* on **any** detected conflict, blocks every state-mutating action and permits
  exactly one: reporting the conflict to the operator.

Note the "any". The ranking exists to *name* the priority in the report, never
to let the agent resolve the contradiction on its own and proceed — deciding
quietly is precisely the behaviour being blocked.

Design principles
-----------------
* No LLM calls, no I/O, mutates nothing — deterministic and O(n log n).
* Conservative in the safe direction: no directives, or directives about
  different subjects, means PROCEED. Any single contradiction blocks.
* Honest boundary: this module *decides*, it does not *enforce*. Callers must
  consult it before writing. Parsing prose into (subject, demand) pairs is also
  the caller's job.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Authority ranking (docs/INSTRUCTION_AUTHORITY.md §2)
# ---------------------------------------------------------------------------

AuthorityLevel = Literal[
    "operator",          # 1 — владелец системы
    "task_contract",     # 2 — что задача явно требует/запрещает
    "test_expectation",  # 3 — поведение, закреплённое зелёным тестом
    "repo_invariant",    # 4 — доктрина и инварианты репозитория
    "local_convention",  # 5 — стиль окружающего кода
    "advisor",           # 6 — ревью, линтер, «так удобнее»
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

# Unknown levels are treated as the weakest possible source rather than
# silently ranked first — an unrecognised label must never win an argument.
_UNKNOWN_RANK = max(AUTHORITY_RANK.values()) + 1


# ---------------------------------------------------------------------------
# Action vocabulary
# ---------------------------------------------------------------------------

GateMode = Literal["proceed", "blocked"]
AllowedAction = Literal["report_conflict"]

# The single action permitted while a conflict is open.
ALLOWED_WHILE_CONFLICTED: AllowedAction = "report_conflict"

# Everything that changes state. This is the turnstile: while a conflict is
# open none of these may run, no matter how confident the instruction was.
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


# ---------------------------------------------------------------------------
# Directives
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Collapse whitespace, lowercase, drop trailing punctuation.

    Only used for *comparison*; the original text is always what gets reported,
    so normalisation can never quietly rewrite what a source actually said.
    """
    flat = " ".join(str(text or "").split()).lower()
    return re.sub(r"[.,;:!?\s]+$", "", flat)


@dataclass(frozen=True)
class Directive:
    """One requirement, and who is making it.

    ``subject`` is what the requirement is *about* (the sort order, the network
    access, the exception). Two directives conflict only when they share a
    subject and demand different things of it.
    """

    source_level: AuthorityLevel
    source_name: str      # "код-ревью PR #214", "docs/AGENT_DOCTRINE.md"
    subject: str          # "порядок вывода"
    demand: str           # "сохранять стабильный порядок"
    locator: str = ""     # file:line / URL, optional
    quote: str = ""       # verbatim wording, optional but strongly preferred

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


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Resolution procedure (docs/INSTRUCTION_AUTHORITY.md §4)
# ---------------------------------------------------------------------------

RESOLUTION_STEPS: tuple[str, ...] = (
    "привести дословно оба требования и их источники",
    "спросить оператора, какой источник имеет силу в этом случае",
    "если по существу прав нижний источник — оператор меняет верхний документ "
    "(спецификацию, контракт, тест), а не агент обходит его в коде",
    "только после письменного решения оператора снять блокировку и внести "
    "правку одним изменением",
    "записать эпизод (инструкция → конфликт → решение) в процедурную память",
)


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------

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
        """True iff ``action`` must NOT run given this outcome.

        In ``proceed`` mode nothing is forbidden — the gate never blocks work
        that has no contradiction behind it.
        """
        return self.mode == "blocked" and action in self.forbidden_actions

    def report(self) -> str:
        """The six-point operator report required by INSTRUCTION_AUTHORITY §4.

        Empty string in proceed mode. Point 4 states what the gate *forbade*;
        proving the working tree is actually untouched is a separate, external
        check (``git diff``) and this text does not claim to be that proof.
        """
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


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def evaluate(directives: Iterable[Directive]) -> InstructionConflictOutcome:
    """Decide PROCEED vs BLOCKED for a set of requirements.

    Two directives conflict when they share a subject and demand different
    things of it. Requirements about different subjects are not a conflict —
    they must simply both be satisfied.

    Any single conflict blocks, including one where the ranking makes the
    winner obvious: the agent's job at that point is to report, not to decide.
    """
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
        # Strongest authority first; ties keep input order so the result is
        # stable and reproducible for the same input.
        ranked = sorted(group, key=lambda item: item.rank)
        strongest = ranked[0]
        strongest_demand = _normalize(strongest.demand)
        for other in ranked[1:]:
            if _normalize(other.demand) == strongest_demand:
                continue  # same requirement restated — not a conflict
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
    """Convenience for the most common shape: a reviewer contradicting the task.

    Exists so the frequent case does not require assembling ``Directive``
    objects by hand at every call site.
    """
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
