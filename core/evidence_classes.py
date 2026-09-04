"""Evidence classes — *what kind* of support a claim actually needs (issue
#119).

``self_analysis`` The agent's reasoning *about* the two classes above. It is
a deliverable, not a source, so it is never promoted to ``verified`` — but
it is also not an unsupported world claim, and deleting it is a defect.

* :func:`is_self_analysis_turn` — is the *turn* a conversational correction
or a request to explain the agent's own previous reply? Requires a prior
turn to exist, so it can never fire on the first message of a session. *
:func:`is_dialogue_scoped_claim` — is this *chunk* a statement about the
preceding interaction, as opposed to a claim about the world that happens to
be phrased in the first person? Both a person marker and a dialogue-object
marker are required, so "Я рекомендую купить X" stays an ordinary world
claim and only "Мой предыдущий ответ не отвечал на вопрос" becomes dialogue-
scoped.

Under-crediting is the deliberately safe direction here: a self-analysis
sentence this module fails to recognise is treated exactly as it was before,
so the worst regression is the status quo. Over-crediting is what would let
an unsupported world claim ride out on a first-person pronoun, which is why
the second marker is mandatory.
"""
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

ALL_EVIDENCE_CLASSES: tuple[EvidenceClass, ...] = (
    "external_world",
    "session_dialogue",
    "trace",
    "self_analysis",
    "generative",
)

#: Evidence kinds (``core.evidence.EvidenceKind``) per class. ``llm_claim`` and
#: ``unknown`` are deliberately absent: an ungrounded model assertion belongs to
#: no evidence class and stays unsupported.
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
    # This run measuring itself. `trace`, not `external_world`: it says what
    # the agent IS running on, never what is true outside it. Added 2026-08-14
    # so a claim about the agent's own body can be verified instead of landing
    # in the same "cannot be determined" as everything else about itself.
    "runtime": "trace",
    # A block the loop read from its own journals: about this agent, never the world.
    "sensor": "trace",
    "session_dialogue": "session_dialogue",
}


def classify_evidence(ev: Any) -> EvidenceClass | None:
    """Which class does this :class:`~core.evidence.Evidence` belong to?

    ``None`` for kinds that support nothing on their own (``llm_claim``,
    ``unknown``, or an unrecognised kind) — the caller must not treat those as
    support of any class.
    """
    kind = str(getattr(ev, "kind", "") or "")
    return _KIND_TO_CLASS.get(kind)


# ── Turn classification ──────────────────────────────────────────────────────

# The operator addressing the agent itself. Without one of these a sentence
# about "an incorrect answer" is about someone else's answer — a server's, an
# API's, a colleague's — and must not disable world-fact verification.
#: Разбор СОБСТВЕННОЙ ОБРАБОТКИ ТЕКУЩЕГО хода. Два условия в одном образце, и
#: оба обязательны: предмет — своя работа (поведение, обработка, маршрутизация,
#: трасса, компоненты системы), и указатель — на ЭТО сообщение/прогон. Порознь
#: они ловят обычные вопросы: «этот проект» — указатель без предмета, «проверь
#: маршрутизацию» — предмет без указателя. Латиница отдельной ветвью: оператор
#: пишет транслитом, и слепота к этому уже стоила подавленного ответа.
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

# The operator saying the previous reply was wrong / must be fixed / explained.
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

# The subject under discussion is the exchange itself, not the world.
_DIALOGUE_OBJECT_RE = re.compile(
    r"(?i)("
    r"ответ\w*|отвеч\w*|вопрос\w*|спрос\w*|сказал\w*|"
    r"реплик\w*|сообщени\w*|диалог\w*|"
    r"answer\w*|repl(?:y|ies|ied)|response\w*|question\w*|"
    r"ask(?:ed|ing)?|said|told|turn|message"
    r")"
)

# First / second person marker inside a *claim* the agent wrote about itself.
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
    """Is this turn a conversational correction / self-analysis request?

    Three conditions, all required:

    1. a prior turn exists in this session (otherwise there is nothing to
       analyse and no dialogue evidence to offer);
    2. the operator addresses the agent in the second person;
    3. the operator says something was wrong, or asks the agent to explain or
       repair its own reply.

    The exact live prompt from issue #119 — «плохо справился Ты не правильно
    ответила некорректно тебе надо тебя починить …» — satisfies all three.
    """
    text = (question or "").strip()
    if not text:
        return SelfAnalysisDecision(False, "empty_question")

    # Вторая дорога, и у неё другая улика. Условие «нужен прошлый ход» верно
    # для просьбы объяснить ПРОШЛЫЙ ответ, но разбор ТЕКУЩЕГО прогона опирается
    # на `trace` — класс из этой же таксономии, существующий с первого
    # сообщения. 2026-08-10 первый ход сессии просил разобрать обработку именно
    # этого сообщения и был вырезан гейтом улик: спрашивали историю там, где
    # улика лежала в журнале.
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
    """Is this claim a statement about the preceding interaction?

    Scope is judged from the claim's own wording, never from a citation the
    model attached to it. A model that writes ``[dialogue:turn_1]`` after
    "the central bank rate is 21%" is asserting scope, not demonstrating it;
    letting that token decide would hand any world claim the transcript's
    support for the cost of one bracket.
    """
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
