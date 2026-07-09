"""Clarification Policy (§3 Cognitive Core — Clarification Policy).

Decides whether the agent should ask the user for clarification before
planning, or whether it should act on a safe default and proceed.

Design principles
-----------------
* No LLM calls — pure regex + heuristics, deterministic, O(n).
* Conservative: when in doubt, ACT (safe default), never block the agent
  unless the question is genuinely ambiguous about a *dangerous* action.
* Clarification is surface-level: ask one short question, not a form.
* Never ask for clarification on purely informational / Q&A requests.

Ambiguity categories
--------------------
MISSING_TARGET   — destructive/write action named but no target specified
                   e.g. "удали", "перезапиши", "очисти" without a path
CONFLICTING_SCOPE — contradictory scope signals in one sentence
                   e.g. "все файлы кроме всего"
UNDERSPECIFIED_GOAL — goal verb detected but no concrete object/target
                   e.g. "сделай что-нибудь с этим"
NONE             — question is clear enough; proceed with planning

Decision
--------
PROCEED          — no clarification needed
ASK              — emit a clarification_request log event and return the
                   question text to the caller so the REPL can display it
                   and re-enter the cycle with augmented input
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# Ambiguity patterns
# ---------------------------------------------------------------------------

AmbiguityKind = Literal[
    "missing_target",
    "conflicting_scope",
    "underspecified_goal",
]

ClarificationDecision = Literal["proceed", "ask"]


@dataclass(frozen=True)
class AmbiguityFinding:
    kind: AmbiguityKind
    evidence: str          # short excerpt that triggered the pattern
    confidence: float      # 0.0–1.0


@dataclass
class ClarificationResult:
    decision: ClarificationDecision
    findings: list[AmbiguityFinding] = field(default_factory=list)
    question: str = ""     # non-empty only when decision == "ask"

    @property
    def should_ask(self) -> bool:
        return self.decision == "ask"


# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------

# Destructive / write verbs that REQUIRE a target to be safe.
# Matches RU and EN variants. Case-insensitive.
_DESTRUCTIVE_VERBS_RU = re.compile(
    r"\b(удал[иьяю]|перезапиши|перепиши|очист[иь]|снеси|снос[иь]"
    r"|затр[иь]|обнул[иь]|сбрось|drop\s+table|rm\b|remove\b"
    r"|delete\b|truncate\b|wipe\b|overwrite\b)\b",
    re.IGNORECASE,
)

# Target signals — a path, filename, quoted string, or pronoun that
# anchors the destructive action to a specific object.
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

# Scope contradictions (e.g. "все файлы кроме всех" / "always except always").
# The same root word (все/всех/всем, all/always, везде/везде) appears on
# BOTH sides of a contrast keyword → clear contradiction.
_SCOPE_CONTRADICTION = re.compile(
    r"\b(все\w*|all|always|никогда|never|везде|nowhere)\b.{0,40}"
    r"\b(кроме|except|но не|but not|исключая|excluding)\b.{0,40}"
    r"\b(все\w*|all|always|никогда|never|везде|nowhere)\b",
    re.IGNORECASE | re.DOTALL,
)

# Underspecified goal: action word + vague placeholder
_VAGUE_GOAL = re.compile(
    r"\b(сделай|сделать|измени|изменить|fix|change|update|modify|do)\b"
    r".{0,25}"
    r"\b(что-нибудь|что-то|anything|something|кое-что|всё это|this stuff)\b",
    re.IGNORECASE,
)

# Safe bypass: if ANY of these patterns appear the question is almost
# certainly a pure Q&A / informational request — never ask for clarification.
_INFORMATIONAL_SIGNALS = re.compile(
    r"\b(что такое|what is|что значит|как работает|how does|расскажи|"
    r"explain|объясни|покажи|show me|список|list|найди|find|search|"
    r"проверь|check|посмотри|look at|прочитай|read|открой|open|"
    r"запусти тесты|run tests|запусти агент|run agent)\b",
    re.IGNORECASE,
)

# Confidence thresholds
_ASK_THRESHOLD = 0.65   # must exceed to trigger clarification


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_clarification(text: str) -> ClarificationResult:
    """Evaluate whether *text* requires clarification before planning.

    Returns a :class:`ClarificationResult` with:
    - ``decision="proceed"`` when the input is clear enough to plan.
    - ``decision="ask"`` when clarification should be requested, plus
      a ``question`` string the REPL can show to the user.

    Pure function — no I/O, no LLM, deterministic.
    """
    # Guard: non-string or empty input is always safe to proceed.
    if not isinstance(text, str) or not text.strip():
        return ClarificationResult(decision="proceed")

    # Guard: truncate to 4096 chars before applying regex to prevent ReDoS.
    if len(text) > 4096:
        text = text[:4096]

    # 1. Fast bypass: informational / Q&A requests never need clarification.
    if _INFORMATIONAL_SIGNALS.search(text):
        return ClarificationResult(decision="proceed")

    findings: list[AmbiguityFinding] = []

    # 2. Conflicting scope
    if _SCOPE_CONTRADICTION.search(text):
        excerpt = _SCOPE_CONTRADICTION.search(text).group(0)[:60]  # type: ignore[union-attr]
        findings.append(
            AmbiguityFinding(
                kind="conflicting_scope",
                evidence=excerpt,
                confidence=0.85,
            )
        )

    # 3. Destructive verb without target
    verb_match = _DESTRUCTIVE_VERBS_RU.search(text)
    if verb_match and not _TARGET_SIGNALS.search(text):
        findings.append(
            AmbiguityFinding(
                kind="missing_target",
                evidence=verb_match.group(0),
                confidence=0.80,
            )
        )

    # 4. Underspecified goal
    if _VAGUE_GOAL.search(text):
        vague_match = _VAGUE_GOAL.search(text)
        findings.append(
            AmbiguityFinding(
                kind="underspecified_goal",
                evidence=vague_match.group(0)[:60],  # type: ignore[union-attr]
                confidence=0.70,
            )
        )

    # 5. Decision: ask only if at least one finding exceeds threshold.
    blocking = [f for f in findings if f.confidence >= _ASK_THRESHOLD]
    if not blocking:
        return ClarificationResult(decision="proceed", findings=findings)

    # Pick the highest-confidence finding to build the question from.
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
