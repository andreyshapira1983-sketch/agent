"""Decide whether to ask a clarifying question before planning or act on a safe default (regex heuristics, no LLM)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

AmbiguityKind = Literal[
    "missing_target",
    "conflicting_scope",
    "underspecified_goal",
]

ClarificationDecision = Literal["proceed", "ask"]


@dataclass(frozen=True)
class AmbiguityFinding:
    kind: AmbiguityKind
    evidence: str
    confidence: float


@dataclass
class ClarificationResult:
    decision: ClarificationDecision
    findings: list[AmbiguityFinding] = field(default_factory=list)
    question: str = ""     # non-empty only when decision == "ask"

    @property
    def should_ask(self) -> bool:
        return self.decision == "ask"



# Destructive/write verbs that need a target (RU and EN, case-insensitive).
_DESTRUCTIVE_VERBS_RU = re.compile(
    r"\b(удал[иьяю]|перезапиши|перепиши|очист[иь]|снеси|снос[иь]"
    r"|затр[иь]|обнул[иь]|сбрось|drop\s+table|rm\b|remove\b"
    r"|delete\b|truncate\b|wipe\b|overwrite\b)\b",
    re.IGNORECASE,
)

# Target signals: path, filename, quoted string or pronoun anchoring the action.
_TARGET_SIGNALS = re.compile(
    r"""(
        ["'][^"']{2,}["']           # quoted string
        | /[\w./\-]{2,}             # unix-like path
        | [A-Za-z]:\\[\w\\\-.]{2,}  # windows path
        | \b[\w\-]+\.(py|txt|json|yaml|yml|md|log|csv|js|ts)\b   # filename
        | \b(это|этот|эту|этим|их|его|её|them|it|this|that)\b     # pronoun
        | \ball\b | \bвсё\b | \bвсе\b                             # scope words
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Scope contradiction: the same root word on both sides of a contrast keyword.
_SCOPE_CONTRADICTION = re.compile(
    r"\b(все\w*|all|always|никогда|never|везде|nowhere)\b.{0,40}"
    r"\b(кроме|except|но не|but not|исключая|excluding)\b.{0,40}"
    r"\b(все\w*|all|always|никогда|never|везде|nowhere)\b",
    re.IGNORECASE | re.DOTALL,
)

# Underspecified goal: action word plus vague placeholder.
_VAGUE_GOAL = re.compile(
    r"\b(сделай|сделать|измени|изменить|fix|change|update|modify|do)\b"
    r".{0,25}"
    r"\b(что-нибудь|что-то|anything|something|кое-что|всё это|this stuff)\b",
    re.IGNORECASE,
)

# Pure Q&A bypass: never ask when any of these match.
_INFORMATIONAL_SIGNALS = re.compile(
    r"\b(что такое|what is|что значит|как работает|how does|расскажи|"
    r"explain|объясни|покажи|show me|список|list|найди|find|search|"
    r"проверь|check|посмотри|look at|прочитай|read|открой|open|"
    r"запусти тесты|run tests|запусти агент|run agent)\b",
    re.IGNORECASE,
)

_ASK_THRESHOLD = 0.65   # must exceed to trigger clarification



def check_clarification(text: str) -> ClarificationResult:
    """Return a ClarificationResult: "proceed", or "ask" with a question; pure and deterministic."""
    if not isinstance(text, str) or not text.strip():
        return ClarificationResult(decision="proceed")

    # Truncate before regex to prevent ReDoS.
    if len(text) > 4096:
        text = text[:4096]

    if _INFORMATIONAL_SIGNALS.search(text):
        return ClarificationResult(decision="proceed")

    findings: list[AmbiguityFinding] = []

    if _SCOPE_CONTRADICTION.search(text):
        excerpt = _SCOPE_CONTRADICTION.search(text).group(0)[:60]  # type: ignore[union-attr]
        findings.append(
            AmbiguityFinding(
                kind="conflicting_scope",
                evidence=excerpt,
                confidence=0.85,
            )
        )

    verb_match = _DESTRUCTIVE_VERBS_RU.search(text)
    if verb_match and not _TARGET_SIGNALS.search(text):
        findings.append(
            AmbiguityFinding(
                kind="missing_target",
                evidence=verb_match.group(0),
                confidence=0.80,
            )
        )

    if _VAGUE_GOAL.search(text):
        vague_match = _VAGUE_GOAL.search(text)
        findings.append(
            AmbiguityFinding(
                kind="underspecified_goal",
                evidence=vague_match.group(0)[:60],  # type: ignore[union-attr]
                confidence=0.70,
            )
        )

    blocking = [f for f in findings if f.confidence >= _ASK_THRESHOLD]
    if not blocking:
        return ClarificationResult(decision="proceed", findings=findings)

    top = max(blocking, key=lambda f: f.confidence)
    question = _build_question(text, top)

    return ClarificationResult(
        decision="ask",
        findings=findings,
        question=question,
    )


def _build_question(text: str, finding: AmbiguityFinding) -> str:
    """Build a short, natural-language clarification question."""
    if finding.kind == "missing_target":
        return (
            f"Уточни, пожалуйста: «{finding.evidence}» — к чему именно это применить? "
            f"Укажи путь, имя файла или объект."
        )
    if finding.kind == "conflicting_scope":
        return (
            "Вижу противоречие в описании области действия. "
            "Уточни: на что именно должно распространяться действие?"
        )
    if finding.kind == "underspecified_goal":
        return (
            f"Поясни, пожалуйста: «{finding.evidence}» — что конкретно нужно сделать? "
            f"Чем подробнее, тем точнее я выполню."
        )
    return "Уточни, пожалуйста, что именно нужно сделать."
