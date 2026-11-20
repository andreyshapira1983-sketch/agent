"""Outgoing-reply judge (stage 1 of the human-conversation rules): pure, mechanical verdict on a (question, reply) pair."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.word_overlap import jaccard as _jaccard

# Small-talk register: greetings, thanks, farewells, "how are you" in RU/EN; whole-word matches only.
_SMALL_TALK_RE = re.compile(
    r"\b(привет|здравствуй(?:те)?|добрый\s+(?:день|вечер)|доброе\s+утро|"
    r"как\s+(?:дела|ты|вы|жизнь)|спасибо|благодарю|пока|до\s+свидания|"
    r"hi|hello|hey|good\s+(?:morning|evening|afternoon)|"
    r"how\s+are\s+you|thanks|thank\s+you|bye|goodbye)\b",
    re.IGNORECASE,
)
# Markers of a "program" in text a human reads: internal contract sections, code fences, leading JSON braces, tracebacks.
_TECHNICAL_MARKS_RE = re.compile(
    r"(^\s*(?:conclusion|facts|unverified|sources|confidence)\s*:|```|"
    r"^\s*[{\[]|traceback \(most recent call last\))",
    re.IGNORECASE | re.MULTILINE,
)
_HUMAN_REPLY_MAX_LEN = 350

_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)

# Repeat: the same reply sent recently in answer to a DIFFERENT question; a repeated question deserves its answer again. Compared as word sets.
_REPEAT_JACCARD = 0.9
_SAME_QUESTION_JACCARD = 0.5


def _tokens(text: str) -> frozenset[str]:
    """Words of 3+ characters plus polarity short words from the canonical dictionary (so "не" is not dropped)."""
    from core.topic_tokens import _POLARITY_WORDS

    lowered = (text or "").lower()
    words = frozenset(re.findall(r"[a-zа-яё0-9]+", lowered))
    return frozenset(re.findall(r"[a-zа-яё0-9]{3,}", lowered)) | (
        words & _POLARITY_WORDS
    )


def classify_register(incoming: str) -> str:
    """small_talk = short human phrase with no business, else substantive.

    Small talk is recognized by subtraction: after removing politeness formulas no substantive sign may remain; no length threshold.
    """
    text = (incoming or "").strip()
    if not text:
        return "substantive"
    if not _SMALL_TALK_RE.search(text):
        return "substantive"
    remainder = _SMALL_TALK_RE.sub(" ", text)
    # Business is any substantive sign (Unicode letter or digit) left over: "PR?", "C?", "γ?" count.
    # Business-as-chatter loses answer evidence, chatter-as-business only keeps a full answer; the leak is accepted.
    if any(ch.isalnum() for ch in remainder):
        return "substantive"
    return "small_talk"


@dataclass(frozen=True)
class ReplyJudgement:
    """Judge verdict: ok | trim | repeat_silence | silence; human_reply is set only for trim; reasons name the rule that fired."""

    verdict: str
    reasons: tuple[str, ...] = ()
    human_reply: str | None = None
    #: Observations (language mismatch etc.): logged, not acted on; there is no mechanical fix.
    observations: tuple[str, ...] = field(default=())


def _conclusion_only(reply: str) -> str | None:
    """Human core of a reply: the Conclusion lines without the other sections."""
    lines: list[str] = []
    in_conclusion = False
    for raw in (reply or "").splitlines():
        low = raw.strip().lower()
        if low.startswith("conclusion:"):
            in_conclusion = True
            rest = raw.strip()[len("conclusion:"):].strip()
            if rest:
                lines.append(rest)
            continue
        if re.match(r"^(facts|unverified|sources|confidence|safety)\s*:", low):
            in_conclusion = False
            continue
        if in_conclusion and raw.strip():
            lines.append(raw.strip())
    return " ".join(lines) or None


def judge_reply(
    incoming: str,
    reply: str,
    recent_exchanges: tuple[tuple[str, str], ...] = (),
) -> ReplyJudgement:
    """Judge a (human question, agent reply) pair; pure and deterministic.

    recent_exchanges are (incoming, reply) pairs; a repeated reply is silenced only when the incoming message differed.
    """
    text = (reply or "").strip()
    if not text:
        return ReplyJudgement("silence", ("пустой ответ — молчание честнее",))

    reply_toks = _tokens(text)
    incoming_toks = _tokens(incoming)
    for old_incoming, old_reply in recent_exchanges:
        if (_jaccard(reply_toks, _tokens(old_reply)) >= _REPEAT_JACCARD
                and _jaccard(incoming_toks, _tokens(old_incoming))
                < _SAME_QUESTION_JACCARD):
            return ReplyJudgement(
                "repeat_silence",
                (
                    ("этот ответ уже уходил на другой вопрос — повтор без "
                     "спроса не разговор, а шум"),
                ),
            )

    observations: list[str] = []
    if _CYRILLIC_RE.search(incoming or "") and not _CYRILLIC_RE.search(text):
        observations.append("собеседник пишет по-русски, ответ без единой "
                            "русской буквы — регистр языка не услышан")

    if classify_register(incoming) == "small_talk" and (
        _TECHNICAL_MARKS_RE.search(text) or len(text) > _HUMAN_REPLY_MAX_LEN
    ):
        human = _conclusion_only(text)
        if human and len(human) <= _HUMAN_REPLY_MAX_LEN:
            return ReplyJudgement(
                "trim",
                ("на человеческую фразу — человеческий ответ, не отчёт",),
                human_reply=human,
                observations=tuple(observations),
            )
        return ReplyJudgement(
            "trim",
            (
                "на человеческую фразу — человеческий ответ, не отчёт",
                ("механически укоротить не вышло — ответ уходит как есть, "
                 "нарушение записано"),
            ),
            observations=tuple(observations),
        )

    if observations:
        return ReplyJudgement("ok", (), observations=tuple(observations))
    return ReplyJudgement("ok")
