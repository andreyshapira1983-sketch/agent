"""Classify operator text into conversation, bounded action, persistent goal or goal control."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ActivityType = Literal[
    "conversation", "bounded_action", "persistent_goal", "goal_control",
]

#: Hypothetical, meta or mechanism-describing framing; vetoes every other class.
_HYPOTHETICAL_RE = re.compile(
    r"(?i)как бы ты|что ты думаешь|что бы ты|расскажи|представь|вообрази"
    r"|если бы|допустим|гипотетически"
    r"|→|=>|что произойдёт|если я (?:напишу|скажу)|должн[аоы]?\s+уметь"
    r"|what do you think|how would you|tell me|imagine|hypothetically"
    r"|what happens if|if i (?:write|say)|should be able",
)

#: A verb inside a quoted or backticked span is mention, not use.
_QUOTE_SPAN_RE = re.compile(r'"[^"\n]{2,400}"|«[^»\n]{2,400}»|`[^`\n]{2,400}`')


def _unquoted(text: str) -> str:
    """Return text with quoted/backticked spans blanked."""
    return _QUOTE_SPAN_RE.sub(lambda m: " " * len(m.group(0)), text)

#: Launching work: both a start verb and a continuity phrase must match.
_START_RE = re.compile(
    r"(?i)\bначни|\bначинай|\bзаймись|\bприступай|\bвозьмись"
    r"|\bstart\b|\bbegin\b|\btake up\b",
)
_CONTINUITY_RE = re.compile(
    r"(?i)продолжай|как свою работу|как работу|на постоянной основе"
    r"|постоянно|дальше сам|веди это|это твоя работа|не останавливайся"
    r"|keep going|as your (?:ongoing )?work|and continue|from now on",
)

#: Managing existing work: a control verb and a work noun.
_CONTROL_RE = re.compile(
    r"(?i)останови|прекрати|приостанови|поставь на паузу|возобнови|отмени"
    r"|\bstop\b|\bpause\b|\bresume\b|\bcancel\b",
)
_WORK_NOUN_RE = re.compile(
    r"(?i)обучени|работ|цель|целью|задач|кампани|изучени"
    r"|\bwork\b|\bgoal\b|\btask\b|\blearning\b|\bcampaign\b",
)

#: Do-it-now imperatives; label only, routing is the same.
_IMMEDIATE_RE = re.compile(
    r"(?i)\bпроверь|\bизмерь|\bзапусти|\bвыполни|\bсделай|\bпокажи"
    r"|\bcheck\b|\brun\b|\bverify\b|\bmeasure\b",
)


@dataclass(frozen=True)
class ActivityDecision:
    activity: ActivityType
    reason: str


# An order leads the utterance; verbs buried mid-text (pasted tails, arguments) do not count.
_HEAD_CHARS = 30


def _leads(pattern: re.Pattern[str], text: str) -> bool:
    m = pattern.search(text)
    return m is not None and m.start() < _HEAD_CHARS


def decide_activity(text: str) -> ActivityDecision:
    """Return one deterministic decision: the hypothetical/meta veto scans the whole text,
    quotes included; directives are read only from the unquoted text."""
    t = (text or "").strip()
    if not t:
        return ActivityDecision("conversation", "empty input")
    if _HYPOTHETICAL_RE.search(t):
        return ActivityDecision(
            "conversation", "hypothetical/meta framing vetoes launching")
    top = _unquoted(t)
    if _leads(_CONTROL_RE, top) and _WORK_NOUN_RE.search(top):
        return ActivityDecision(
            "goal_control", "control verb leads + work noun")
    if _leads(_START_RE, top) and _CONTINUITY_RE.search(top):
        return ActivityDecision(
            "persistent_goal", "start verb leads + continuity contract")
    if _leads(_IMMEDIATE_RE, top):
        return ActivityDecision(
            "bounded_action", "do-it-now imperative; routed to the loop")
    return ActivityDecision("conversation", "default")
