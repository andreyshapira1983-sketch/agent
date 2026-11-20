"""Evidence classes: what kind of support a claim needs, plus self-analysis and dialogue-scope detection."""
# Under-crediting is the safe direction: unrecognised self-analysis is treated as before. The second marker
# is mandatory so a world claim cannot ride out on a first-person pronoun.
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

EvidenceClass = Literal[
    "external_world",
    "session_dialogue",
    "trace",
    "self_analysis",
    "generative",
]

#: Evidence kinds per class; llm_claim and unknown are absent on purpose: an ungrounded model assertion stays unsupported.
_KIND_TO_CLASS: dict[str, EvidenceClass] = {
    "file": "external_world",
    "web_page": "external_world",
    "web_search_hit": "external_world",
    "tool_output": "external_world",
    "test_result": "external_world",
    "shell_output": "external_world",
    "diff_preview": "external_world",
    "memory": "external_world",
    "user_explicit": "external_world",
    "log_event": "trace",
    # This run measuring itself: "trace", not "external_world"; says what the agent runs on, not what is true outside.
    "runtime": "trace",
    # A block the loop read from its own journals: about this agent, never the world.
    "sensor": "trace",
    "session_dialogue": "session_dialogue",
}


def classify_evidence(ev: Any) -> EvidenceClass | None:
    """Class of an Evidence, or None for kinds that support nothing on their own (llm_claim, unknown, unrecognised)."""
    kind = str(getattr(ev, "kind", "") or "")
    return _KIND_TO_CLASS.get(kind)



# The operator addressing the agent itself; otherwise "an incorrect answer" is someone else's and must not disable world-fact verification.
#: Analysis of the agent's own processing of the CURRENT turn: needs both a subject (own work) and a pointer (this message/run);
#: Latin transliteration has its own branch.
_CURRENT_RUN_INTROSPECTION_RE = re.compile(
    r"(?:"
    r"(?:сво[ей]|твоё|твое|твоей|твоего|твоя|твои)\s+"
    r"(?:фактическ\w+\s+)?"
    r"(?:поведени\w*|обработк\w*|трасс\w*|маршрутизаци\w*|систем\w*)"
    r"|(?:sво[ej]|svoe|svoyu|tvoey|tvoyey|tvoey|tvoego)\s+"
    r"(?:fakticheskoy?\s+|fakticheskoe\s+)?"
    r"(?:povedeni\w*|obrabotk\w*|trasse?\w*|marshrutizaci\w*|sistem\w*)"
    r"|your\s+(?:actual\s+|own\s+)?"
    r"(?:behaviou?r|processing|trace|routing|system)"
    r")"
    r"|(?:"
    r"(?:этого|это)\s+сообщени\w*"
    r"|(?:etogo|eto)\s+soobshcheni\w*"
    r"|this\s+(?:message|request|turn|run)"
    r")"
    r"(?=[\s\S]{0,400}?(?:"
    r"компонент\w*|трасс\w*|маршрут\w*|определил|обработк\w*"
    r"|komponent\w*|trass\w*|marshrut\w*|opredelil|obrabotk\w*"
    r"|component|trace|rout|determine|process"
    r"))",
    re.IGNORECASE,
)

_AGENT_ADDRESSED_RE = re.compile(
    r"(?i)(?:^|\W)("
    r"ты|тебя|тебе|тобой|твой|твоя|твоё|твое|твои|твоего|твоём|твоем|"
    r"вы|вас|вам|ваш|ваша|ваше|ваши|вашего|"
    r"you|your|yours|yourself"
    r")(?:\W|$)"
)

_CORRECTION_RE = re.compile(
    r"(?i)("
    r"не\s*правильн\w*|неправильн\w*|"
    r"не\s*коррект\w*|некоррект\w*|"
    r"не\s*верн\w*|неверн\w*|"
    r"ошиб\w*|плохо\s+справ\w*|плохой\s+ответ|"
    r"почин\w*|исправ\w*|перепрограмм\w*|переделай|"
    r"ты\s+не\s+ответ\w*|не\s+ответил\w*|не\s+то\s+ответ\w*|"
    r"wrong|incorrect|mistaken|mistake|"
    r"did\s+not\s+answer|didn'?t\s+answer|bad\s+answer|"
    r"you\s+failed|fix\s+yourself|reprogram"
    r")"
)

_DIALOGUE_OBJECT_RE = re.compile(
    r"(?i)("
    r"ответ\w*|отвеч\w*|вопрос\w*|спрос\w*|сказал\w*|"
    r"реплик\w*|сообщени\w*|диалог\w*|"
    r"answer\w*|repl(?:y|ies|ied)|response\w*|question\w*|"
    r"ask(?:ed|ing)?|said|told|turn|message"
    r")"
)

_CLAIM_PERSON_RE = re.compile(
    r"(?i)(?:^|\W)("
    r"я|мне|меня|мой|моя|моё|мое|мои|моего|моём|моем|"
    r"ты|тебе|тебя|твой|твоя|твоё|твое|вы|вам|ваш|ваша|ваше|ваши|"
    r"i|me|my|mine|you|your"
    r")(?:\W|$)"
)

@dataclass(frozen=True)
class SelfAnalysisDecision:
    """Outcome of classifying one turn. Pure data; no side effects."""

    is_self_analysis: bool
    reason: str
    markers: tuple[str, ...] = ()

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "is_self_analysis": self.is_self_analysis,
            "reason": self.reason,
            "markers": list(self.markers),
        }


def is_self_analysis_turn(
    question: str,
    *,
    has_prior_turn: bool,
) -> SelfAnalysisDecision:
    """Is this turn a conversational correction or self-analysis request?

    Requires a prior turn, a second-person address, and a statement that something was wrong or a request to explain/repair the reply.
    """
    text = (question or "").strip()
    if not text:
        return SelfAnalysisDecision(False, "empty_question")

    # Second road: analysis of the CURRENT run rests on trace evidence, which exists from the first message, so no prior turn is needed.
    own_run = _CURRENT_RUN_INTROSPECTION_RE.search(text)
    if own_run is not None:
        return SelfAnalysisDecision(
            True, "current_run_introspection", (own_run.group(0).lower(),)
        )

    if not has_prior_turn:
        return SelfAnalysisDecision(False, "no_prior_turn")

    addressed = _AGENT_ADDRESSED_RE.search(text)
    if addressed is None:
        return SelfAnalysisDecision(False, "agent_not_addressed")
    correction = _CORRECTION_RE.search(text)
    if correction is None:
        return SelfAnalysisDecision(False, "no_correction_marker")

    markers = (addressed.group(1).lower(), correction.group(1).lower())
    return SelfAnalysisDecision(True, "conversational_correction", markers)


def is_dialogue_scoped_claim(text: str) -> bool:
    """Is this claim about the preceding interaction? Judged from its own wording, never from a citation the model attached."""
    body = text or ""
    if not body.strip():
        return False
    return bool(
        _CLAIM_PERSON_RE.search(body) and _DIALOGUE_OBJECT_RE.search(body)
    )


def dialogue_evidence_present(chain: Any) -> bool:
    """Does this provenance chain carry session-dialogue evidence?"""
    for ev in getattr(chain, "evidences", ()) or ():
        if classify_evidence(ev) == "session_dialogue":
            return True
    return False
